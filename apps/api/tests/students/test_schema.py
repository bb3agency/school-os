"""sis schema guarantees at the database layer (migration 0008; SEC-001, FR-STU-005, FR-STU-007,
ADR-0013 §7, docs/12 §4.4-4.10). Statements run as ``sos_app`` unless stated otherwise."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, Table, inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError

from app.core.db import context_free_session, tenant_session
from app.core.model_base import Base
from app.students import models

pytestmark = pytest.mark.db

MODELS = [
    models.Student,
    models.Enrollment,
    models.AttributeDefinition,
    models.AttributeValue,
    models.StudentProfile,
    models.Guardian,
    models.StudentGuardian,
]


@pytest.mark.parametrize("model", MODELS, ids=lambda m: m.__tablename__)
def test_FR_STU_001_model_matches_database(model: type[Base], admin_engine: Engine) -> None:
    table = model.__table__
    assert isinstance(table, Table)
    insp = inspect(admin_engine)
    db_cols = {c["name"]: c for c in insp.get_columns(table.name, schema=table.schema)}
    assert set(db_cols) == {c.name for c in table.columns}
    for col in table.columns:
        assert db_cols[col.name]["nullable"] == col.nullable, f"{table.name}.{col.name}"
    pk = insp.get_pk_constraint(table.name, schema=table.schema)["constrained_columns"]
    assert sorted(pk) == sorted(c.name for c in table.primary_key.columns)


# --- attribute definitions: global rows ------------------------------------------------------


def test_FR_STU_006_global_definitions_are_seeded_and_read_only(
    world: Any, app_engine: Engine
) -> None:
    with tenant_session(world.a.tenant_id) as s:
        rows = dict(
            s.execute(
                text(
                    "SELECT key, classification FROM sis.attribute_definitions "
                    "WHERE tenant_id IS NULL"
                )
            ).all()
        )
        assert rows["full_name"] == "C2"
        assert rows["aadhaar_last4"] == "C3"
        assert {"aadhaar_name_as_printed", "health_notes", "address", "category"} <= set(rows)
        assert (
            s.execute(
                text("UPDATE sis.attribute_definitions SET label_en = 'X' WHERE tenant_id IS NULL")
            ).rowcount
            == 0
        )
        assert (
            s.execute(
                text("DELETE FROM sis.attribute_definitions WHERE tenant_id IS NULL")
            ).rowcount
            == 0
        )
        # Stealing a global row into the tenant is refused too (not visible to UPDATE).
        assert (
            s.execute(
                text(
                    "UPDATE sis.attribute_definitions SET tenant_id = core.current_tenant() "
                    "WHERE key = 'full_name'"
                )
            ).rowcount
            == 0
        )
    with (
        pytest.raises(ProgrammingError, match="row-level security"),
        tenant_session(world.a.tenant_id) as s,
    ):
        s.execute(
            text(
                "INSERT INTO sis.attribute_definitions (id, tenant_id, key, data_type, "
                "classification, canonical_policy, label_en, label_te) VALUES (gen_random_uuid(), "
                "NULL, 'sneaky', 'text', 'C2', '{}', 'S', 'S')"
            )
        )
    with context_free_session() as s:
        assert s.execute(text("SELECT count(*) FROM sis.attribute_definitions")).scalar_one() == 0


def test_FR_STU_006_school_attributes_are_private_to_the_school(
    world: Any, app_engine: Engine
) -> None:
    key = f"house_{uuid.uuid4().hex[:8]}"
    with tenant_session(world.a.tenant_id) as s:
        s.execute(
            text(
                "INSERT INTO sis.attribute_definitions (id, tenant_id, key, data_type, "
                "classification, canonical_policy, label_en, label_te) VALUES (gen_random_uuid(), "
                "core.current_tenant(), :k, 'text', 'C2', '{}', 'House', 'House')"
            ),
            {"k": key},
        )
    with tenant_session(world.b.tenant_id) as s:
        found = s.execute(
            text("SELECT count(*) FROM sis.attribute_definitions WHERE key = :k"), {"k": key}
        ).scalar_one()
        assert found == 0


# --- composite tenant foreign keys ------------------------------------------------------------


CROSS = {
    "enrollments.section": (
        "INSERT INTO sis.enrollments (id, tenant_id, student_id, section_id, academic_year_id) "
        "VALUES (gen_random_uuid(), :ta, :a_student, :b_section, :a_old_year)",
        "enrollments_section_fk",
    ),
    "enrollments.year": (
        "INSERT INTO sis.enrollments (id, tenant_id, student_id, section_id, academic_year_id) "
        "VALUES (gen_random_uuid(), :ta, :a_student, :a_section, :b_year)",
        "enrollments_academic_year_fk",
    ),
    "enrollments.student": (
        "INSERT INTO sis.enrollments (id, tenant_id, student_id, section_id, academic_year_id) "
        "VALUES (gen_random_uuid(), :ta, :b_student, :a_section, :a_year)",
        "enrollments_student_fk",
    ),
    "attribute_values.student": (
        "INSERT INTO sis.attribute_values (id, tenant_id, student_id, attribute_key, source, "
        "value_text, recorded_by) VALUES (gen_random_uuid(), :ta, :b_student, 'mother_tongue', "
        "'parent_form', 'X', :user)",
        "attribute_values_student_fk",
    ),
    "student_guardians.guardian": (
        "INSERT INTO sis.student_guardians (tenant_id, student_id, guardian_id, relationship) "
        "VALUES (:ta, :a_student, :b_guardian, 'father')",
        "student_guardians_guardian_fk",
    ),
    "student_profiles.student": (
        "INSERT INTO sis.student_profiles (tenant_id, student_id) VALUES (:ta, :b_student)",
        "student_profiles_student_fk",
    ),
}


@pytest.mark.parametrize("case", sorted(CROSS))
def test_ADR_0013_sis_cannot_reference_other_schools_rows(
    case: str, world: Any, shared: dict[str, Any], app_engine: Engine
) -> None:
    sql, constraint = CROSS[case]
    params = {
        "ta": world.a.tenant_id,
        "a_student": shared["s9c"],
        "a_section": world.a.ids["section_10a"],
        "a_year": world.a.ids["year"],
        "a_old_year": world.a.ids["old_year"],
        "b_student": shared["b_sb"],
        "b_section": world.b.ids["section_9a"],
        "b_year": world.b.ids["year"],
        "b_guardian": shared["b_gb"],
        "user": world.a.people["owner"].user_id,
    }
    with pytest.raises(IntegrityError, match=constraint), tenant_session(world.a.tenant_id) as s:
        s.execute(text(sql), params)


# --- value rows: C3 exclusivity, current uniqueness, immutable history ------------------------


def _value_row(admin: Engine, student_id: uuid.UUID, key: str) -> Any:
    with admin.connect() as c:
        return c.execute(
            text(
                "SELECT * FROM sis.attribute_values WHERE student_id = :s AND attribute_key = :k "
                "AND superseded_by IS NULL"
            ),
            {"s": student_id, "k": key},
        ).one()


def test_FR_STU_007_ciphertext_and_plaintext_are_exclusive(
    world: Any, shared: dict[str, Any], app_engine: Engine
) -> None:
    with (
        pytest.raises(IntegrityError, match="attribute_values_c3_exclusive"),
        tenant_session(world.a.tenant_id) as s,
    ):
        s.execute(
            text(
                "INSERT INTO sis.attribute_values (id, tenant_id, student_id, attribute_key, "
                "source, value_text, value_ciphertext, key_version, recorded_by) VALUES "
                "(gen_random_uuid(), :t, :s, 'caste', 'manual_entry', 'plain', '\\x01', 1, :u)"
            ),
            {"t": world.a.tenant_id, "s": shared["s9c"], "u": world.a.people["owner"].user_id},
        )


def test_FR_STU_002_one_current_value_per_source(
    world: Any, shared: dict[str, Any], app_engine: Engine
) -> None:
    with pytest.raises(IntegrityError, match="av_current"), tenant_session(world.a.tenant_id) as s:
        s.execute(
            text(
                "INSERT INTO sis.attribute_values (id, tenant_id, student_id, attribute_key, "
                "source, value_text, recorded_by) VALUES (gen_random_uuid(), :t, :s, 'full_name', "
                "'admission_register', 'Another', :u)"
            ),
            {"t": world.a.tenant_id, "s": shared["s9c"], "u": world.a.people["owner"].user_id},
        )


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE sis.attribute_values SET value_text = 'Changed' WHERE id = :i",
        "UPDATE sis.attribute_values SET recorded_by = recorded_by WHERE id = :i",
        "DELETE FROM sis.attribute_values WHERE id = :i",
    ],
)
def test_FR_STU_005_app_cannot_edit_or_delete_recorded_values(
    statement: str, world: Any, shared: dict[str, Any], admin_engine: Engine, app_engine: Engine
) -> None:
    row = _value_row(admin_engine, shared["s9c"], "full_name")
    with (
        pytest.raises(ProgrammingError, match="permission denied"),
        tenant_session(world.a.tenant_id) as s,
    ):
        s.execute(text(statement), {"i": row.id})


def test_FR_STU_005_trigger_blocks_value_edits_even_for_the_owner(
    world: Any, shared: dict[str, Any], admin_engine: Engine
) -> None:
    row = _value_row(admin_engine, shared["s9c"], "full_name")
    with pytest.raises(DBAPIError, match="immutable"), admin_engine.begin() as c:
        c.execute(
            text("UPDATE sis.attribute_values SET value_text = 'Changed' WHERE id = :i"),
            {"i": row.id},
        )
    ct = _value_row(admin_engine, shared["s9a"], "health_notes")
    with pytest.raises(DBAPIError, match="re-encryption"), admin_engine.begin() as c:
        c.execute(
            text("UPDATE sis.attribute_values SET value_ciphertext = '\\x01' WHERE id = :i"),
            {"i": ct.id},
        )


def test_FR_STU_005_superseded_rows_are_frozen(
    world: Any, admin_engine: Engine, app_engine: Engine
) -> None:
    import sys

    sw = sys.modules["sos_test_student_world"]
    from app.students import service as students

    sid = sw.create(world.a, name="Synthetica Frozen History", section_key=None)
    ctx = sw.admin_ctx(world.a)
    with tenant_session(world.a.tenant_id, ctx.user_id) as db:
        first = students.record_value(db, ctx, sid, "mother_tongue", "parent_form", "Telugu")
        second = students.record_value(db, ctx, sid, "mother_tongue", "parent_form", "Urdu")
    assert second.superseded == first.id
    # Verification columns are updatable by the app, but not on a superseded row.
    with (
        pytest.raises(DBAPIError, match="immutable"),
        tenant_session(world.a.tenant_id, ctx.user_id) as s,
    ):
        s.execute(
            text(
                "UPDATE sis.attribute_values SET verification_status = 'verified', "
                "verified_at = now(), verified_by = :u WHERE id = :i"
            ),
            {"i": first.id, "u": ctx.user_id},
        )
    with (
        pytest.raises(DBAPIError, match="immutable"),
        tenant_session(world.a.tenant_id, ctx.user_id) as s,
    ):
        s.execute(
            text("UPDATE sis.attribute_values SET superseded_by = NULL WHERE id = :i"),
            {"i": first.id},
        )
    with tenant_session(world.a.tenant_id, ctx.user_id) as s:
        n = s.execute(
            text(
                "UPDATE sis.attribute_values SET verification_status = 'verified', "
                "verified_at = now(), verified_by = :u WHERE id = :i"
            ),
            {"i": second.id, "u": ctx.user_id},
        ).rowcount
    assert n == 1


def test_SEC_001_sis_tables_are_empty_without_tenant_context(
    shared: dict[str, Any], app_engine: Engine
) -> None:
    with context_free_session() as s:
        for table in (
            "sis.students",
            "sis.enrollments",
            "sis.attribute_values",
            "sis.student_profiles",
            "sis.guardians",
            "sis.student_guardians",
            "sis.attribute_definitions",
        ):
            assert s.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0, table
