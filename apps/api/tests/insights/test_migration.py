"""0035_student_insights: schema facts, database checks and a populated round trip (CLAUDE.md
§6.1, §6.4, §6.12, §8; docs/05 §6.4; FR-ATT-*, FR-MRK-*, FR-EW-*).

A fresh database is migrated to head, seeded with a synthetic school (``seed-synthetic``), and
given a student with attendance, an exam with marks, a behaviour note, a flag with an action and
school settings; the walk head -> previous revision -> head must succeed with that data present
and leave every other table untouched.
"""

from __future__ import annotations

import io
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import SecretStr
from sqlalchemy import Engine, Table, create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from app.academics import models as academics_models
from app.core import db as core_db
from app.core.config import Environment, KeyWrapperKind, Settings
from app.core.crypto import LocalDevKeyWrapper
from app.core.model_base import Base
from app.devtools import seed_synthetic as cli
from app.devtools import seeder
from app.insights import models as insights_models

pytestmark = pytest.mark.db
DB = "schoolos_insights_migration"
REVISION = "0035_student_insights"
ACADEMIC_TABLES = {
    ("sis", "attendance_marks"),
    ("sis", "exams"),
    ("sis", "exam_marks"),
}
INSIGHT_TABLES = {
    ("sis", "behaviour_notes"),
    ("sis", "insight_flags"),
    ("sis", "flag_actions"),
    ("sis", "insight_settings"),
}
TABLES = ACADEMIC_TABLES | INSIGHT_TABLES
NEW_PERMISSIONS = (
    "attendance.record",
    "attendance.read",
    "exam.manage",
    "marks.record",
    "marks.read",
    "insights.note",
    "insights.act",
    "insights.manage",
)
SETTINGS = Settings(
    env=Environment.CI,
    key_wrapper=KeyWrapperKind.LOCAL_DEV,
    local_dev_master_key=SecretStr("synthetic-ci-master-key-0123456789abcdef"),
)


def _scalar(engine: Engine, sql: str, **params: Any) -> Any:
    with engine.connect() as c:
        return c.execute(text(sql), params).scalar_one()


def _grants(engine: Engine, role: str, schema: str, table: str) -> set[str]:
    with engine.connect() as c:
        return set(
            c.execute(
                text(
                    "SELECT privilege_type FROM information_schema.role_table_grants "
                    "WHERE grantee = :r AND table_schema = :s AND table_name = :t"
                ),
                {"r": role, "s": schema, "t": table},
            ).scalars()
        )


def _update_columns(engine: Engine, table: str) -> set[str]:
    with engine.connect() as c:
        return set(
            c.execute(
                text(
                    "SELECT column_name FROM information_schema.column_privileges "
                    "WHERE grantee = 'sos_app' AND table_schema = 'sis' AND table_name = :t "
                    "AND privilege_type = 'UPDATE'"
                ),
                {"t": table},
            ).scalars()
        )


@pytest.mark.parametrize(
    "model",
    [
        academics_models.AttendanceMark,
        academics_models.Exam,
        academics_models.ExamMark,
        insights_models.BehaviourNote,
        insights_models.InsightFlag,
        insights_models.FlagAction,
        insights_models.InsightSettings,
    ],
    ids=lambda m: m.__tablename__,
)
def test_M5_models_match_database(model: type[Base], admin_engine: Engine) -> None:
    table = model.__table__
    assert isinstance(table, Table)
    insp = inspect(admin_engine)
    db_cols = {c["name"]: c for c in insp.get_columns(table.name, schema=table.schema)}
    assert set(db_cols) == {c.name for c in table.columns}
    for col in table.columns:
        assert db_cols[col.name]["nullable"] == col.nullable, f"{table.name}.{col.name}"


def test_SEC_001_tables_force_rls_with_purge_policy_and_narrow_grants(
    admin_engine: Engine,
) -> None:
    with admin_engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT n.nspname, c.relname, c.relrowsecurity, c.relforcerowsecurity "
                "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'sis' AND c.relname = ANY(:names)"
            ),
            {"names": [t for _, t in TABLES]},
        ).all()
        policies = {
            (r.schemaname, r.tablename, r.policyname)
            for r in c.execute(
                text(
                    "SELECT schemaname, tablename, policyname FROM pg_policies "
                    "WHERE schemaname = 'sis' AND tablename = ANY(:names)"
                ),
                {"names": [t for _, t in TABLES]},
            )
        }
    assert {(r.nspname, r.relname) for r in rows} == TABLES
    assert all(r.relrowsecurity and r.relforcerowsecurity for r in rows)
    for schema, table in TABLES:
        assert (schema, table, "tenant_isolation") in policies
        assert (schema, table, "offboarding_purge") in policies  # ADR-0029
        app = _grants(admin_engine, "sos_app", schema, table)
        assert "TRUNCATE" not in app
        assert "UPDATE" not in app, f"{table}: column grants only"
        assert {"SELECT", "DELETE"} <= _grants(admin_engine, "sos_purger", schema, table)
    # School records are corrected, never deleted by the app (FR-ATT-002, FR-MRK-002).
    for _, table in ACADEMIC_TABLES:
        assert "DELETE" not in _grants(admin_engine, "sos_app", "sis", table)
    # Actions are an append-only log (FR-EW-007); settings are one row per school.
    assert "DELETE" not in _grants(admin_engine, "sos_app", "sis", "flag_actions")
    assert "DELETE" not in _grants(admin_engine, "sos_app", "sis", "insight_settings")
    # Restricted (C3) insights are not for the reporting role (PRV-004).
    for table in ("behaviour_notes", "insight_flags", "flag_actions", "insight_settings"):
        assert _grants(admin_engine, "sos_readonly", "sis", table) == set()


def test_FR_EW_010_note_text_changes_only_by_key_rotation(admin_engine: Engine) -> None:
    assert _update_columns(admin_engine, "behaviour_notes") == {"body_ciphertext", "key_version"}
    assert _update_columns(admin_engine, "flag_actions") == {"note_ciphertext", "key_version"}
    # What a rule saw and when it raised the flag never changes.
    flags = _update_columns(admin_engine, "insight_flags")
    assert not flags & {"rule", "basis", "evidence", "student_id", "raised_on", "raised_by"}
    assert {"status", "owner_membership_id", "first_action_at", "closed_at"} <= flags


def test_M5_new_permissions_are_in_the_catalog(admin_engine: Engine) -> None:
    with admin_engine.connect() as c:
        rows = {
            r.key: r
            for r in c.execute(
                text("SELECT key, step_up, is_platform FROM core.permissions WHERE key = ANY(:k)"),
                {"k": [*NEW_PERMISSIONS, "insights.read"]},
            )
        }
    assert set(rows) == {*NEW_PERMISSIONS, "insights.read"}
    assert rows["insights.manage"].step_up is True
    assert not any(r.is_platform for r in rows.values())


# --- database checks ------------------------------------------------------------------------------


@dataclass
class _Ids:
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    membership_id: uuid.UUID
    year_id: uuid.UUID
    section_id: uuid.UUID
    student_id: uuid.UUID


def _school(admin: Engine) -> _Ids:
    """A student enrolled in a section of an existing school (superuser setup, synthetic)."""
    with admin.begin() as c:
        row = c.execute(
            text(
                "SELECT m.tenant_id, m.user_id, m.id FROM core.memberships m "
                "WHERE m.status = 'active' ORDER BY m.created_at LIMIT 1"
            )
        ).one()
        tenant_id, user_id, membership_id = row[0], row[1], row[2]
        year, klass, section, student = (uuid.uuid4() for _ in range(4))
        c.execute(
            text(
                "INSERT INTO core.academic_years (id, tenant_id, label, starts_on, ends_on) "
                "VALUES (:i, :t, :l, '2031-06-01', '2032-03-31')"
            ),
            {"i": year, "t": tenant_id, "l": f"M{uuid.uuid4().hex[:6]}"},
        )
        c.execute(
            text(
                "INSERT INTO core.classes (id, tenant_id, code, display_en, display_te, "
                "sort_order) VALUES (:i, :t, :c, 'Synthetic class', 'సింథెటిక్', 999)"
            ),
            {"i": klass, "t": tenant_id, "c": f"Z{uuid.uuid4().hex[:5].upper()}"},
        )
        c.execute(
            text(
                "INSERT INTO core.sections (id, tenant_id, class_id, academic_year_id, name) "
                "VALUES (:i, :t, :c, :y, 'A')"
            ),
            {"i": section, "t": tenant_id, "c": klass, "y": year},
        )
        c.execute(
            text("INSERT INTO sis.students (id, tenant_id, status) VALUES (:i, :t, 'active')"),
            {"i": student, "t": tenant_id},
        )
    return _Ids(tenant_id, user_id, membership_id, year, section, student)


def _insert_rows(admin: Engine, ids: _Ids) -> dict[str, uuid.UUID]:
    out = {k: uuid.uuid4() for k in ("mark", "exam", "exam_mark", "note", "flag", "action")}
    blob = b"\x01\x00\x01" + b"\x00" * 40
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO sis.attendance_marks (id, tenant_id, student_id, section_id, "
                "on_date, status, recorded_by) VALUES (:i, :t, :s, :sec, '2031-07-01', "
                "'absent', :u)"
            ),
            {
                "i": out["mark"],
                "t": ids.tenant_id,
                "s": ids.student_id,
                "sec": ids.section_id,
                "u": ids.user_id,
            },
        )
        c.execute(
            text(
                "INSERT INTO sis.exams (id, tenant_id, academic_year_id, name, held_on, "
                "created_by) VALUES (:i, :t, :y, 'Synthetic unit test', '2031-07-15', :u)"
            ),
            {"i": out["exam"], "t": ids.tenant_id, "y": ids.year_id, "u": ids.user_id},
        )
        c.execute(
            text(
                "INSERT INTO sis.exam_marks (id, tenant_id, exam_id, student_id, section_id, "
                "subject, max_marks, marks, recorded_by) VALUES (:i, :t, :e, :s, :sec, "
                "'Mathematics', 50, 21.5, :u)"
            ),
            {
                "i": out["exam_mark"],
                "t": ids.tenant_id,
                "e": out["exam"],
                "s": ids.student_id,
                "sec": ids.section_id,
                "u": ids.user_id,
            },
        )
        c.execute(
            text(
                "INSERT INTO sis.behaviour_notes (id, tenant_id, student_id, section_id, "
                "category, noted_on, body_ciphertext, key_version, created_by, "
                "created_by_membership) VALUES (:i, :t, :s, :sec, 'concern', '2031-07-02', :b, "
                "1, :u, :m)"
            ),
            {
                "i": out["note"],
                "t": ids.tenant_id,
                "s": ids.student_id,
                "sec": ids.section_id,
                "b": blob,
                "u": ids.user_id,
                "m": ids.membership_id,
            },
        )
        c.execute(
            text(
                "INSERT INTO sis.insight_flags (id, tenant_id, student_id, section_id, "
                "indicator, rule, rules_version, basis, evidence, owner_membership_id, "
                "raised_on, due_on) VALUES (:i, :t, :s, :sec, 'attendance', "
                "'attendance_streak', 1, 'run:2031-07-01', '{\"days\": 3}', :m, "
                "'2031-07-03', '2031-07-10')"
            ),
            {
                "i": out["flag"],
                "t": ids.tenant_id,
                "s": ids.student_id,
                "sec": ids.section_id,
                "m": ids.membership_id,
            },
        )
        c.execute(
            text(
                "INSERT INTO sis.flag_actions (id, tenant_id, flag_id, kind, acted_on, "
                "created_by, created_by_membership) VALUES (:i, :t, :f, 'called_parent', "
                "'2031-07-04', :u, :m)"
            ),
            {
                "i": out["action"],
                "t": ids.tenant_id,
                "f": out["flag"],
                "u": ids.user_id,
                "m": ids.membership_id,
            },
        )
        c.execute(
            text(
                "INSERT INTO sis.insight_settings (id, tenant_id, rules, updated_by) "
                'VALUES (:i, :t, \'{"attendance_streak": {"threshold": 4}}\', :u) '
                "ON CONFLICT (tenant_id) DO NOTHING"
            ),
            {"i": uuid.uuid4(), "t": ids.tenant_id, "u": ids.user_id},
        )
    return out


def test_FR_ATT_001_one_status_per_student_and_day(world: Any, admin_engine: Engine) -> None:
    ids = _school(admin_engine)
    rows = _insert_rows(admin_engine, ids)
    assert rows
    with pytest.raises(DBAPIError, match="attendance_marks_one_per_day"), admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO sis.attendance_marks (id, tenant_id, student_id, section_id, "
                "on_date, status, recorded_by) VALUES (:i, :t, :s, :sec, '2031-07-01', "
                "'present', :u)"
            ),
            {
                "i": uuid.uuid4(),
                "t": ids.tenant_id,
                "s": ids.student_id,
                "sec": ids.section_id,
                "u": ids.user_id,
            },
        )
    with (
        pytest.raises(DBAPIError, match="attendance_marks_status_check"),
        admin_engine.begin() as c,
    ):
        c.execute(
            text(
                "INSERT INTO sis.attendance_marks (id, tenant_id, student_id, section_id, "
                "on_date, status, recorded_by) VALUES (:i, :t, :s, :sec, '2031-07-05', "
                "'holiday', :u)"
            ),
            {
                "i": uuid.uuid4(),
                "t": ids.tenant_id,
                "s": ids.student_id,
                "sec": ids.section_id,
                "u": ids.user_id,
            },
        )


def test_FR_MRK_002_marks_stay_within_max_and_absent_has_no_marks(
    world: Any, admin_engine: Engine
) -> None:
    ids = _school(admin_engine)
    rows = _insert_rows(admin_engine, ids)
    base = (
        "INSERT INTO sis.exam_marks (id, tenant_id, exam_id, student_id, section_id, subject, "
        "max_marks, marks, absent, recorded_by) VALUES (:i, :t, :e, :s, :sec, :subj, 50, :mk, "
        ":ab, :u)"
    )
    params = {
        "t": ids.tenant_id,
        "e": rows["exam"],
        "s": ids.student_id,
        "sec": ids.section_id,
        "u": ids.user_id,
    }
    for subject, marks, absent, constraint in (
        ("Science", 51, False, "exam_marks_marks_range"),
        ("English", None, False, "exam_marks_absent_has_no_marks"),
        ("Telugu", 10, True, "exam_marks_absent_has_no_marks"),
    ):
        with pytest.raises(DBAPIError, match=constraint), admin_engine.begin() as c:
            c.execute(
                text(base),
                {**params, "i": uuid.uuid4(), "subj": subject, "mk": marks, "ab": absent},
            )


def test_FR_EW_003_one_open_flag_per_student_and_rule(world: Any, admin_engine: Engine) -> None:
    ids = _school(admin_engine)
    _insert_rows(admin_engine, ids)
    insert = (
        "INSERT INTO sis.insight_flags (id, tenant_id, student_id, section_id, indicator, rule, "
        "rules_version, basis, raised_on, due_on) VALUES (:i, :t, :s, :sec, 'attendance', "
        "'attendance_streak', 1, :b, '2031-07-20', '2031-07-27')"
    )
    params = {"t": ids.tenant_id, "s": ids.student_id, "sec": ids.section_id}
    with pytest.raises(DBAPIError, match="insight_flags_one_open"), admin_engine.begin() as c:
        c.execute(text(insert), {**params, "i": uuid.uuid4(), "b": "run:2031-07-18"})
    with admin_engine.begin() as c:
        c.execute(
            text(
                "UPDATE sis.insight_flags SET status = 'closed', closed_at = now(), "
                "closed_by = :u, close_reason = 'improved', first_action_at = now() "
                "WHERE tenant_id = :t AND student_id = :s"
            ),
            {"t": ids.tenant_id, "s": ids.student_id, "u": ids.user_id},
        )
    # Closed: a new run may be flagged, but never the same basis again.
    with pytest.raises(DBAPIError, match="insight_flags_basis_key"), admin_engine.begin() as c:
        c.execute(text(insert), {**params, "i": uuid.uuid4(), "b": "run:2031-07-01"})
    with admin_engine.begin() as c:
        c.execute(text(insert), {**params, "i": uuid.uuid4(), "b": "run:2031-07-18"})


def test_FR_EW_007_a_closed_flag_has_its_reason_and_an_action_time(
    world: Any, admin_engine: Engine
) -> None:
    ids = _school(admin_engine)
    rows = _insert_rows(admin_engine, ids)
    with pytest.raises(DBAPIError, match="insight_flags_closed_fields"), admin_engine.begin() as c:
        c.execute(
            text(
                "UPDATE sis.insight_flags SET status = 'closed', first_action_at = now() "
                "WHERE id = :f"
            ),
            {"f": rows["flag"]},
        )
    with pytest.raises(DBAPIError, match="insight_flags_actioned"), admin_engine.begin() as c:
        c.execute(
            text("UPDATE sis.insight_flags SET status = 'in_progress' WHERE id = :f"),
            {"f": rows["flag"]},
        )


# --- populated round trip -------------------------------------------------------------------------


@dataclass
class _Walk:
    cfg: Config
    admin: Engine


@pytest.fixture
def populated(test_database: Any, make_alembic_config: Callable[[str], Config]) -> Iterator[_Walk]:
    url = test_database.create_fresh(DB)
    cfg = make_alembic_config(url)
    command.upgrade(cfg, "head")
    engines = {
        "app": create_engine(test_database.url_for("sos_app", "test-app-pw", DB)),
        "platform": create_engine(test_database.url_for("sos_platform", "test-platform-pw", DB)),
        "admin": create_engine(
            make_url(test_database.admin_url).set(database=DB).render_as_string(hide_password=False)
        ),
    }
    saved = {k: core_db.get_engine(k) for k in ("app", "platform")}
    core_db.set_engine("app", engines["app"])
    core_db.set_engine("platform", engines["platform"])
    try:
        code = cli.main(
            ["--tenants", "1", "--code-prefix", "insight"],
            settings=SETTINGS,
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            wrapper=LocalDevKeyWrapper(SETTINGS),
            owner_bootstrap=seeder.PlatformOwnerBootstrap(),
        )
        assert code == cli.EXIT_OK
        yield _Walk(cfg, engines["admin"])
    finally:
        for kind, engine in saved.items():
            core_db.set_engine(kind, engine)
        for engine in engines.values():
            engine.dispose()


def test_CLAUDE_6_12_student_insights_migration_reversible_with_data(populated: _Walk) -> None:
    admin = populated.admin
    before = ScriptDirectory.from_config(populated.cfg).get_revision(REVISION).down_revision
    assert before == "0034_circulars"
    ids = _school(admin)
    _insert_rows(admin, ids)
    students = _scalar(admin, "SELECT count(*) FROM sis.students")
    sections = _scalar(admin, "SELECT count(*) FROM core.sections")
    events = _scalar(admin, "SELECT count(*) FROM audit.events")

    command.downgrade(populated.cfg, before)
    for schema, table in TABLES:
        assert _scalar(admin, f"SELECT to_regclass('{schema}.{table}') IS NULL") is True
    assert _scalar(admin, "SELECT version_num FROM ops.alembic_version") == before
    assert _scalar(admin, "SELECT count(*) FROM sis.students") == students
    assert _scalar(admin, "SELECT count(*) FROM core.sections") == sections
    assert _scalar(admin, "SELECT count(*) FROM audit.events") == events
    # insights.read predates M5 (0004 seed) and stays; the M5 keys go when no role holds them.
    assert _scalar(admin, "SELECT count(*) FROM core.permissions WHERE key = 'insights.read'") == 1

    command.upgrade(populated.cfg, "head")
    assert _scalar(admin, "SELECT count(*) FROM sis.attendance_marks") == 0
    assert _scalar(admin, "SELECT count(*) FROM core.permissions WHERE key = 'insights.act'") == 1
    command.downgrade(populated.cfg, before)
    command.upgrade(populated.cfg, "head")
