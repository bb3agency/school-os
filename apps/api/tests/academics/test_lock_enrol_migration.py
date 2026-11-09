"""0084_exam_lock_enrol on a populated database (FT-01, FT-09; lead decision D84; FR-ATT-015;
CLAUDE.md §6.12).

Two synthetic schools are seeded (``seed-synthetic``). Below 0083, enrolments of every shape are
added; the upgrade backfills their start dates with the import rule (admission date inside the
year: explicit; moves and dates a person gave: explicit, kept; other defaulted dates: kept,
implicit) and the walk 0083 -> 0082 -> 0083 succeeds with exam history rows present.
"""

from __future__ import annotations

import datetime as dt
import io
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

from app.core import db as core_db
from app.core.config import Environment, KeyWrapperKind, Settings
from app.core.crypto import LocalDevKeyWrapper
from app.devtools import seed_synthetic as cli
from app.devtools import seeder

pytestmark = pytest.mark.db
DB = "schoolos_lock_enrol_migration"
REV = "0084_exam_lock_enrol"
BELOW = "0083_fee_tax_cert"
SETTINGS = Settings(
    env=Environment.CI,
    key_wrapper=KeyWrapperKind.LOCAL_DEV,
    local_dev_master_key=SecretStr("synthetic-ci-master-key-0123456789abcdef"),
)


@pytest.fixture
def populated(
    test_database: Any, make_alembic_config: Callable[[str], Config]
) -> Iterator[tuple[Config, Engine]]:
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
            ["--tenants", "2", "--code-prefix", "lockenrol"],
            settings=SETTINGS,
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            wrapper=LocalDevKeyWrapper(SETTINGS),
            owner_bootstrap=seeder.PlatformOwnerBootstrap(),
        )
        assert code == cli.EXIT_OK
        yield cfg, engines["admin"]
    finally:
        for kind, engine in saved.items():
            core_db.set_engine(kind, engine)
        for engine in engines.values():
            engine.dispose()


def _one(admin: Engine, sql: str, **params: Any) -> Any:
    with admin.connect() as c:
        return c.execute(text(sql), params).one()


def _student(c: Any, tenant: uuid.UUID) -> uuid.UUID:
    sid = uuid.uuid4()
    c.execute(
        text("INSERT INTO sis.students (id, tenant_id, status) VALUES (:i, :t, 'active')"),
        {"i": sid, "t": tenant},
    )
    return sid


def _enrol(c: Any, row: dict[str, Any]) -> uuid.UUID:
    eid = uuid.uuid4()
    c.execute(
        text(
            "INSERT INTO sis.enrollments (id, tenant_id, student_id, section_id, academic_year_id, "
            "status, started_on, ended_on, created_at) VALUES (:i, :t, :s, :sec, :y, :status, "
            ":start, :end, :created)"
        ),
        {"i": eid, "end": None, "status": "active", **row},
    )
    return eid


def test_FR_ATT_015_backfill_and_round_trip_on_a_populated_database(
    populated: tuple[Config, Engine],
) -> None:
    cfg, admin = populated
    command.downgrade(cfg, BELOW)
    place = _one(
        admin,
        "SELECT s.tenant_id, s.id AS section_id, y.id AS year_id, y.starts_on, y.ends_on "
        "FROM core.sections s JOIN core.academic_years y "
        "ON y.tenant_id = s.tenant_id AND y.id = s.academic_year_id "
        "ORDER BY y.starts_on DESC, s.id LIMIT 1",
    )
    user = _one(admin, "SELECT id FROM core.users ORDER BY id LIMIT 1").id
    tenant, starts = place.tenant_id, place.starts_on
    admitted = starts + dt.timedelta(days=40)
    created = dt.datetime.combine(starts + dt.timedelta(days=90), dt.time(6), tzinfo=dt.UTC)
    created_day = created.date()
    base = {"t": tenant, "sec": place.section_id, "y": place.year_id, "created": created}
    with admin.begin() as c:
        with_adm, without, given, mover = (_student(c, tenant) for _ in range(4))
        c.execute(
            text(
                "INSERT INTO sis.attribute_values (id, tenant_id, student_id, attribute_key, "
                "source, value_date, recorded_by) VALUES (:i, :t, :s, 'admission_date', "
                "'admission_register', :d, :u)"
            ),
            {"i": uuid.uuid4(), "t": tenant, "s": with_adm, "d": admitted, "u": user},
        )
        e_adm = _enrol(c, {**base, "s": with_adm, "start": created_day})
        e_without = _enrol(c, {**base, "s": without, "start": created_day})
        e_given = _enrol(c, {**base, "s": given, "start": starts + dt.timedelta(days=10)})
        moved_on = starts + dt.timedelta(days=60)
        _enrol(
            c,
            {
                **base,
                "s": mover,
                "status": "transferred",
                "start": starts,
                "end": moved_on,
                "created": created - dt.timedelta(days=80),
            },
        )
        e_moved = _enrol(c, {**base, "s": mover, "start": moved_on})
    command.upgrade(cfg, REV)

    def state(eid: uuid.UUID) -> tuple[dt.date, bool]:
        row = _one(
            admin,
            "SELECT started_on, started_on_explicit FROM sis.enrollments WHERE id = :e",
            e=eid,
        )
        return row.started_on, row.started_on_explicit

    assert state(e_adm) == (admitted, True)
    assert state(e_without) == (created_day, False)
    assert state(e_given) == (starts + dt.timedelta(days=10), True)
    assert state(e_moved) == (moved_on, True)
    # RLS is forced again on every table the backfill read.
    forced = _one(
        admin,
        "SELECT bool_and(relforcerowsecurity) AS ok FROM pg_class WHERE oid IN "
        "('sis.enrollments'::regclass, 'sis.attribute_values'::regclass, "
        "'core.academic_years'::regclass)",
    )
    assert forced.ok is True
    perm = _one(admin, "SELECT step_up FROM core.permissions WHERE key = 'exam.unlock'")
    assert perm.step_up is True

    # Round trip with an exam lock and a history row present.
    exam, mark = uuid.uuid4(), uuid.uuid4()
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO sis.exams (id, tenant_id, academic_year_id, name, held_on, "
                "created_by, locked_at, locked_by) VALUES (:i, :t, :y, 'Synthetic SA1', :d, :u, "
                "now(), :u)"
            ),
            {"i": exam, "t": tenant, "y": place.year_id, "d": starts, "u": user},
        )
        c.execute(
            text(
                "INSERT INTO sis.exam_marks (id, tenant_id, exam_id, student_id, section_id, "
                "subject, max_marks, marks, recorded_by) VALUES (:i, :t, :e, :s, :sec, 'Maths', "
                "50, 40, :u)"
            ),
            {"i": mark, "t": tenant, "e": exam, "s": with_adm, "sec": place.section_id, "u": user},
        )
        c.execute(
            text(
                "INSERT INTO sis.exam_mark_history (id, tenant_id, exam_id, mark_id, student_id, "
                "section_id, subject, previous_max_marks, previous_marks, previous_absent, "
                "new_max_marks, new_marks, new_absent, source, changed_by) VALUES (:i, :t, :e, "
                ":m, :s, :sec, 'Maths', 50, 30, false, 50, 40, false, 'mark', :u)"
            ),
            {
                "i": uuid.uuid4(),
                "t": tenant,
                "e": exam,
                "m": mark,
                "s": with_adm,
                "sec": place.section_id,
                "u": user,
            },
        )
    command.downgrade(cfg, BELOW)
    assert _one(admin, "SELECT to_regclass('sis.exam_mark_history') IS NULL AS gone").gone
    assert _one(admin, "SELECT count(*) AS n FROM sis.exam_marks WHERE id = :m", m=mark).n == 1
    command.upgrade(cfg, REV)
    assert state(e_adm) == (admitted, True)
