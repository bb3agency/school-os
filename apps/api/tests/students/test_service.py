"""Student service behaviour (US-301, FR-STU-001..008, FR-STU-012, BR-01, invariant 7)."""

from __future__ import annotations

import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.db import tenant_session
from app.core.errors import Conflict, NotFound, PreconditionFailed, ValidationFailed
from app.core.redaction import verhoeff_check_digit
from app.students import service as students
from app.students.schemas import EnrollmentIn, GuardianCreate, GuardianPatch, RevealIn

pytestmark = pytest.mark.db
SW = sys.modules["sos_test_student_world"]
W = SW.W


def aadhaar() -> str:
    body = "23456789012"
    return body + verhoeff_check_digit(body)


@pytest.fixture
def fresh(world: Any, app_engine: Engine) -> uuid.UUID:
    sid: uuid.UUID = SW.create(world.a, name="Kommineni Venkata Sai", section_key="section_9a")
    return sid


def run(world: Any, fn: Any, *args: Any, ctx: Any = None, **kwargs: Any) -> Any:
    ctx = ctx or SW.admin_ctx(world.a)
    with tenant_session(world.a.tenant_id, ctx.user_id) as db:
        return fn(db, ctx, *args, **kwargs)


def events(admin: Engine, world: Any, action: str, resource_id: uuid.UUID) -> list[dict[str, Any]]:
    return [
        e
        for e in W.audit_events(admin, world.a.tenant_id, action)
        if e["resource_id"] == resource_id
    ]


def test_FR_STU_001_create_student_records_sources_and_projection(
    world: Any, fresh: uuid.UUID, admin_engine: Engine
) -> None:
    out = run(world, students.get_profile, fresh)
    assert out.status == "active"
    name = out.canonical["full_name"]
    assert (name.value, name.source, name.verified, name.provisional) == (
        "Kommineni Venkata Sai",
        "admission_register",
        False,
        True,
    )
    assert out.enrollment is not None
    assert out.enrollment.label == "IX-A"
    assert [v.source for v in out.values["full_name"]] == ["admission_register"]
    with admin_engine.connect() as c:
        profile = c.execute(
            text("SELECT * FROM sis.student_profiles WHERE student_id = :s"), {"s": fresh}
        ).one()
    assert profile.full_name_norm == "KOMMINENI VENKATA SAI"
    assert profile.current_section_id == world.a.ids["section_9a"]
    assert profile.father_name_norm == "KOMMINENI RAMANA"
    created = events(admin_engine, world, "student.created", fresh)
    assert len(created) == 1
    summary = created[0]["summary"]
    assert summary["value_count"] == 5
    assert "full_name" in summary["attribute_keys"]
    assert "Kommineni" not in str(created)


def test_FR_STU_005_new_values_supersede_and_history_is_kept(
    world: Any, fresh: uuid.UUID, admin_engine: Engine
) -> None:
    first = run(world, students.record_value, fresh, "mother_tongue", "parent_form", "Telugu")
    same = run(world, students.record_value, fresh, "mother_tongue", "parent_form", " Telugu ")
    assert same.id == first.id, "an identical observation is not a new value"
    second = run(world, students.record_value, fresh, "mother_tongue", "parent_form", "Urdu")
    assert second.superseded == first.id
    history = run(world, students.value_history, fresh, "mother_tongue")
    assert [(v.value, v.current) for v in history] == [("Urdu", True), ("Telugu", False)]
    assert history[1].superseded_by == second.id
    recorded = events(admin_engine, world, "student.value.recorded", fresh)
    assert [e["summary"]["value_id"] for e in recorded] == [str(first.id), str(second.id)]
    assert "Urdu" not in str(recorded)
    assert "Telugu" not in str(recorded)


def test_BR_01_identity_register_value_needs_a_change_request(world: Any, fresh: uuid.UUID) -> None:
    with pytest.raises(students.IdentityChangeRequired) as exc:
        run(
            world, students.record_value, fresh, "full_name", "admission_register", "K. Venkata Sai"
        )
    assert exc.value.status == 403
    assert exc.value.code == "identity_change_required"
    # Re-sending the identical register value (e.g. an import re-run) is a harmless no-op.
    again = run(
        world,
        students.record_value,
        fresh,
        "full_name",
        "admission_register",
        "Kommineni Venkata Sai",
    )
    assert again.superseded is None
    with pytest.raises(students.IdentityChangeRequired):
        run(
            world,
            students.record_value,
            fresh,
            "dob",
            "udise_plus",
            "2012-03-15",
            verification="verified",
        )
    # Observations from other sources are allowed and only produce conflicts.
    run(world, students.record_value, fresh, "full_name", "udise_plus", "Venkata Sai K")
    # A first register value of an identity attribute is recorded unverified.
    run(world, students.record_value, fresh, "admission_date", "admission_register", "2019-06-12")
    out = run(world, students.get_profile, fresh)
    assert out.canonical["full_name"].conflicts == ["udise_plus"]
    assert out.canonical["full_name"].value == "Kommineni Venkata Sai"
    assert out.canonical["admission_date"].provisional is True
    with pytest.raises(students.IdentityChangeRequired):
        run(
            world,
            students.record_value,
            fresh,
            "admission_date",
            "admission_register",
            "2019-06-13",
        )


def test_BR_01_approved_change_request_records_a_verified_value(
    world: Any, fresh: uuid.UUID, admin_engine: Engine
) -> None:
    cr, evidence = uuid.uuid4(), uuid.uuid4()
    approver = SW.ctx_for(world.a.tenant_id, world.a.people["principal"], "principal")
    out = run(
        world,
        students.record_verified_identity_value,
        fresh,
        "dob",
        "2012-03-15",
        change_request_id=cr,
        evidence_document_id=evidence,
        ctx=approver,
    )
    profile = run(world, students.get_profile, fresh)
    dob = profile.canonical["dob"]
    assert (dob.value, dob.verified, dob.provisional) == ("2012-03-15", True, False)
    history = run(world, students.value_history, fresh, "dob")
    assert [h.value for h in history] == ["2012-03-15", "2012-03-14"]
    assert history[0].change_request_id == cr
    assert history[0].verified_by == approver.user_id
    recorded = events(admin_engine, world, "student.value.recorded", fresh)[-1]["summary"]
    assert recorded["change_request_id"] == str(cr)
    assert recorded["verification"] == "verified"
    assert out.superseded == history[1].id
    with pytest.raises(ValidationFailed):
        run(
            world,
            students.record_verified_identity_value,
            fresh,
            "mother_tongue",
            "Telugu",
            change_request_id=cr,
            evidence_document_id=evidence,
        )


def test_FR_STU_003_verify_non_identity_values_only(
    world: Any, fresh: uuid.UUID, admin_engine: Engine
) -> None:
    rec = run(world, students.record_value, fresh, "nationality", "parent_form", "Indian")
    out = run(world, students.verify_value, fresh, rec.id, "verified")
    assert out.verification_status == "verified"
    assert out.verified_by is not None
    name_id = SW.current_value_id(admin_engine, fresh, "full_name", "admission_register")
    with pytest.raises(students.IdentityChangeRequired):
        run(world, students.verify_value, fresh, name_id)
    newer = run(world, students.record_value, fresh, "nationality", "parent_form", "Indian citizen")
    with pytest.raises(Conflict) as exc:
        run(world, students.verify_value, fresh, rec.id)
    assert exc.value.code == "value_superseded"
    assert newer.superseded == rec.id
    with pytest.raises(NotFound):
        run(world, students.verify_value, fresh, uuid.uuid4())
    assert events(admin_engine, world, "student.value.verified", fresh)[0]["summary"]["status"] == (
        "verified"
    )


def test_FR_STU_012_record_value_rejects_full_aadhaar(world: Any, fresh: uuid.UUID) -> None:
    for key, source, value in (
        ("caste", "parent_form", f"ref {aadhaar()}"),
        ("aadhaar_last4", "aadhaar_as_printed", aadhaar()),
        ("aadhaar_name_as_printed", "aadhaar_as_printed", aadhaar()),
    ):
        with pytest.raises(ValidationFailed) as exc:
            run(world, students.record_value, fresh, key, source, value)
        assert exc.value.errors[0]["code"] == "aadhaar_full_number_rejected"
        assert exc.value.errors[0]["message_key"] == "errors.aadhaar_last4_only"


def test_optimistic_version_and_status(world: Any, fresh: uuid.UUID, admin_engine: Engine) -> None:
    v = SW.version(admin_engine, "sis.students", fresh)
    with pytest.raises(PreconditionFailed):
        run(
            world,
            students.record_value,
            fresh,
            "religion",
            "parent_form",
            "X",
            expected_version=v + 5,
        )
    rec = run(
        world, students.record_value, fresh, "religion", "parent_form", "X", expected_version=v
    )
    assert rec.student_version == v + 1
    out = run(world, students.update_student_status, fresh, "left", expected_version=v + 1)
    assert out.status == "left"
    assert out.version == v + 2
    with pytest.raises(PreconditionFailed):
        run(world, students.update_student_status, fresh, "active", expected_version=v)
    changed = events(admin_engine, world, "student.status_changed", fresh)
    assert changed[0]["summary"] == {"from": "active", "to": "left"}


def test_FR_STU_001_admission_number_is_unique_per_school(world: Any, app_engine: Engine) -> None:
    adm = f"ADM/{uuid.uuid4().hex[:6]}"
    sid = SW.create(world.a, name="Synthetica First", section_key=None, admission_no=adm)
    assert run(world, students.get_profile, sid).admission_no == adm
    with pytest.raises(Conflict) as exc:
        SW.create(world.a, name="Synthetica Second", section_key=None, admission_no=adm)
    assert exc.value.code == "duplicate_admission_no"
    # Another school may use the same number.
    SW.create(world.b, name="Synthetica Other", section_key=None, admission_no=adm)


def test_enrolment_transfer_within_a_year(
    world: Any, fresh: uuid.UUID, admin_engine: Engine
) -> None:
    moved = run(world, students.enrol, fresh, EnrollmentIn(section_id=world.a.ids["section_9c"]))
    assert moved.section_id == world.a.ids["section_9c"]
    with admin_engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT section_id, status FROM sis.enrollments WHERE student_id = :s "
                "ORDER BY created_at"
            ),
            {"s": fresh},
        ).all()
    assert [r.status for r in rows] == ["transferred", "active"]
    assert run(world, students.get_profile, fresh).enrollment.label == "IX-C"
    with pytest.raises(Conflict):
        run(world, students.enrol, fresh, EnrollmentIn(section_id=world.a.ids["section_9c"]))
    with pytest.raises(ValidationFailed):
        run(world, students.enrol, fresh, EnrollmentIn(section_id=world.b.ids["section_9a"]))
    old = run(world, students.enrol, fresh, EnrollmentIn(section_id=world.a.ids["section_10a"]))
    assert old.academic_year_id == world.a.ids["year"]
    assert len(W.audit_events(admin_engine, world.a.tenant_id, "enrollment.transferred")) >= 2


def test_canonical_and_source_values_for_dq(world: Any, shared: dict[str, Any]) -> None:
    with tenant_session(world.a.tenant_id) as db:
        canon = students.canonical_values(db, [shared["s9a"]], ["full_name", "health_notes", "dob"])
        sens = students.canonical_values(
            db, [shared["s9a"]], ["health_notes"], include_sensitive=True
        )
        per = students.source_values(db, [shared["s9a"]], ["full_name", "aadhaar_last4"])
    assert set(canon[shared["s9a"]]) == {"full_name", "dob"}, "C3 skipped by default"
    assert sens[shared["s9a"]]["health_notes"].value == "Synthetic asthma note"
    assert per[shared["s9a"]]["full_name"]["admission_register"].norm == "SYNTHETICA VENKATA SAI"
    assert "aadhaar_last4" not in per[shared["s9a"]]


def test_SEC_015_scope_helpers(world: Any, shared: dict[str, Any]) -> None:
    ct = SW.ctx_for(
        world.a.tenant_id,
        world.a.people["class_teacher"],
        "class_teacher",
        section_ids=frozenset({world.a.ids["section_9a"]}),
    )
    with tenant_session(world.a.tenant_id) as db:
        visible = students.list_students_in_scope(db, ct)
        assert shared["s9a"] in visible
        assert shared["s9c"] not in visible
        with pytest.raises(NotFound):
            students.get_profile(db, ct, shared["s9c"])
        with pytest.raises(NotFound):
            students.get_profile(db, ct, shared["b_sb"])
        admin = SW.admin_ctx(world.a)
        assert shared["s9c"] in students.list_students_in_scope(
            db, admin, class_ids=[world.a.ids["class_ix"]]
        )
        assert shared["s10a"] not in students.list_students_in_scope(
            db, admin, section_ids=[world.a.ids["section_9a"]]
        )


def test_FR_STU_008_guardians_encrypted_linked_and_updated(
    world: Any, fresh: uuid.UUID, admin_engine: Engine
) -> None:
    g = run(
        world,
        students.add_guardian,
        fresh,
        GuardianCreate(
            relationship="mother",
            full_name="Synthetica Sarada",
            phone="+91 98765 11111",
            is_primary=True,
        ),
    )
    assert g.has_phone
    assert g.phone == "••••"
    assert g.is_primary
    with admin_engine.connect() as c:
        row = c.execute(text("SELECT * FROM sis.guardians WHERE id = :g"), {"g": g.id}).one()
    assert b"9876511111" not in bytes(row.phone_ciphertext)
    assert row.phone_blind_index is not None
    sibling = SW.create(world.a, name="Synthetica Sibling", section_key="section_9a")
    linked = run(
        world,
        students.add_guardian,
        sibling,
        GuardianCreate(relationship="mother", guardian_id=g.id),
    )
    assert linked.id == g.id
    updated = run(
        world,
        students.update_guardian,
        fresh,
        g.id,
        GuardianPatch(phone="9876522222", address="Synthetic lane"),
        expected_version=g.version,
    )
    assert updated.version == g.version + 1
    assert updated.has_address
    with pytest.raises(PreconditionFailed):
        run(
            world,
            students.update_guardian,
            fresh,
            g.id,
            GuardianPatch(),
            expected_version=g.version,
        )
    revealed = run(
        world,
        students.reveal_sensitive,
        fresh,
        RevealIn(attribute_key="guardian_phone", guardian_id=g.id),
    )
    assert revealed.value == "9876522222"
    assert revealed.display == "+91 98765 22222"
    with pytest.raises(ValidationFailed) as exc:
        run(
            world,
            students.add_guardian,
            fresh,
            GuardianCreate(
                relationship="guardian", full_name="Synthetica X", address=f"H.No {aadhaar()}"
            ),
        )
    assert exc.value.errors == [
        {
            "field": "address",
            "code": "aadhaar_full_number_rejected",
            "message_key": "errors.aadhaar_last4_only",
        }
    ]
    with pytest.raises(NotFound):
        run(
            world,
            students.update_guardian,
            sibling,
            uuid.uuid4(),
            GuardianPatch(),
            expected_version=1,
        )


def test_US_301_AC3_reveal_is_audited_without_the_value(
    world: Any, shared: dict[str, Any], admin_engine: Engine
) -> None:
    out = run(
        world, students.reveal_sensitive, shared["s9a"], RevealIn(attribute_key="aadhaar_last4")
    )
    assert (out.value, out.display, out.source) == ("4821", "XXXX XXXX 4821", "aadhaar_as_printed")
    ev = events(admin_engine, world, "student.sensitive_revealed", shared["s9a"])[-1]
    assert ev["summary"]["attribute_key"] == "aadhaar_last4"
    assert "4821" not in str(ev)
    with pytest.raises(ValidationFailed):
        run(world, students.reveal_sensitive, shared["s9a"], RevealIn(attribute_key="full_name"))
    with pytest.raises(NotFound):
        run(world, students.reveal_sensitive, shared["s9c"], RevealIn(attribute_key="health_notes"))
