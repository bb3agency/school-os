"""0064_portal_calendar: schema facts, database checks and a populated round trip (CLAUDE.md
§6.1, §6.12; docs/05 §16.6; ADR-0053; FR-RDY-004..008).

A fresh database is migrated to head, seeded with a synthetic school (``seed-synthetic``), given
a confirmed date, a reminder and a portal task; the walk head -> previous revision -> head must
succeed with that data present, keep the portal task as a manual task and leave every other
table untouched.
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
from sqlalchemy.exc import DBAPIError, ProgrammingError

from app.circulars import models as circular_models
from app.core import db as core_db
from app.core.config import Environment, KeyWrapperKind, Settings
from app.core.crypto import LocalDevKeyWrapper
from app.devtools import seed_synthetic as cli
from app.devtools import seeder
from app.dq import models

pytestmark = pytest.mark.db
DB = "schoolos_calendar_migration"
REVISION = "0064_portal_calendar"
TABLES = ("portal_calendar_dates", "portal_reminders")
SETTINGS = Settings(
    env=Environment.CI,
    key_wrapper=KeyWrapperKind.LOCAL_DEV,
    local_dev_master_key=SecretStr("synthetic-ci-master-key-0123456789abcdef"),
)


def _scalar(engine: Engine, sql: str, **params: Any) -> Any:
    with engine.connect() as c:
        return c.execute(text(sql), params).scalar_one()


def _grants(engine: Engine, role: str, table: str) -> set[str]:
    with engine.connect() as c:
        return set(
            c.execute(
                text(
                    "SELECT privilege_type FROM information_schema.role_table_grants "
                    "WHERE grantee = :r AND table_schema = 'ops' AND table_name = :t"
                ),
                {"r": role, "t": table},
            ).scalars()
        )


@pytest.mark.parametrize(
    "table", [models.portal_calendar_dates, models.portal_reminders], ids=lambda t: t.name
)
def test_ADR_0053_models_match_database(table: Table, admin_engine: Engine) -> None:
    db_cols = {
        c["name"]: c for c in inspect(admin_engine).get_columns(table.name, schema=table.schema)
    }
    assert set(db_cols) == {c.name for c in table.columns}
    for col in table.columns:
        assert db_cols[col.name]["nullable"] == col.nullable, f"{table.name}.{col.name}"


def test_ADR_0053_task_model_has_the_portal_event_key(admin_engine: Engine) -> None:
    table = circular_models.Task.__table__
    assert isinstance(table, Table)
    cols = {c["name"] for c in inspect(admin_engine).get_columns("tasks", schema="ops")}
    assert "portal_event_key" in cols
    assert cols == {c.name for c in table.columns}


def test_SEC_001_tables_force_rls_with_purge_policy(admin_engine: Engine) -> None:
    with admin_engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'ops' AND c.relname = ANY(:names)"
            ),
            {"names": list(TABLES)},
        ).all()
        policies = {
            (r.tablename, r.policyname)
            for r in c.execute(
                text(
                    "SELECT tablename, policyname FROM pg_policies "
                    "WHERE schemaname = 'ops' AND tablename = ANY(:names)"
                ),
                {"names": list(TABLES)},
            )
        }
    assert {r.relname for r in rows} == set(TABLES)
    assert all(r.relrowsecurity and r.relforcerowsecurity for r in rows)
    for table in TABLES:
        assert (table, "tenant_isolation") in policies
        assert (table, "offboarding_purge") in policies  # ADR-0029
        assert {"SELECT", "DELETE"} <= _grants(admin_engine, "sos_purger", table)
        assert not _grants(admin_engine, "sos_platform", table)
    # Reminders are append-only for the app; confirmations may be undone.
    assert _grants(admin_engine, "sos_app", "portal_reminders") == {"SELECT", "INSERT"}
    assert {"SELECT", "INSERT", "DELETE"} <= _grants(
        admin_engine, "sos_app", "portal_calendar_dates"
    )
    assert "TRUNCATE" not in _grants(admin_engine, "sos_app", "portal_calendar_dates")


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE ops.portal_reminders SET notified = 0 WHERE false",
        "DELETE FROM ops.portal_reminders WHERE false",
        "UPDATE ops.portal_calendar_dates SET event_key = 'x' WHERE false",
        "UPDATE ops.portal_calendar_dates SET academic_year_id = academic_year_id WHERE false",
        "UPDATE ops.tasks SET portal_event_key = 'x' WHERE false",
        "UPDATE ops.tasks SET source = 'manual' WHERE false",
    ],
)
def test_FR_RDY_008_app_role_cannot_rewrite_reminders_or_keys(
    app_engine: Engine, statement: str
) -> None:
    with app_engine.connect() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(uuid.uuid4())})
        with pytest.raises(ProgrammingError, match="permission denied"):
            c.execute(text(statement))
        c.rollback()


def test_ADR_0053_permission_is_in_the_catalog(admin_engine: Engine) -> None:
    with admin_engine.connect() as c:
        row = c.execute(
            text(
                "SELECT step_up, is_platform FROM core.permissions "
                "WHERE key = 'readiness.calendar.manage'"
            )
        ).one()
    assert not row.step_up
    assert not row.is_platform


# --- database checks ------------------------------------------------------------------------


@dataclass
class _Ids:
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    membership_id: uuid.UUID
    year_id: uuid.UUID


def _school(admin: Engine) -> _Ids:
    with admin.begin() as c:
        tenant_id, user_id, membership_id = c.execute(
            text(
                "SELECT m.tenant_id, m.user_id, m.id FROM core.memberships m "
                "JOIN core.academic_years y ON y.tenant_id = m.tenant_id "
                "WHERE m.status = 'active' ORDER BY m.created_at LIMIT 1"
            )
        ).one()
        year_id: uuid.UUID = c.execute(
            text("SELECT id FROM core.academic_years WHERE tenant_id = :t LIMIT 1"),
            {"t": tenant_id},
        ).scalar_one()
    return _Ids(tenant_id, user_id, membership_id, year_id)


DATE_INSERT = (
    "INSERT INTO ops.portal_calendar_dates (id, tenant_id, academic_year_id, event_key, label, "
    "opens_on, closes_on, profile_keys, source_kind, confirmed_by, confirmed_by_membership) "
    "VALUES (:i, :t, :y, :k, :l, :o, :c, CAST(:p AS text[]), :s, :u, :m)"
)


def _date(ids: _Ids, **over: Any) -> dict[str, Any]:
    base = {
        "i": uuid.uuid4(),
        "t": ids.tenant_id,
        "y": ids.year_id,
        "k": f"synthetic_{uuid.uuid4().hex[:8]}",
        "l": None,
        "o": "2027-02-01",
        "c": "2027-02-05",
        "p": "{}",
        "s": "portal",
        "u": ids.user_id,
        "m": ids.membership_id,
    }
    return {**base, **over}


def _school_b(world: Any) -> _Ids:
    owner = world.b.people["owner"]
    return _Ids(world.b.tenant_id, owner.user_id, owner.membership_id, world.b.ids["year"])


def test_FR_RDY_004_dates_are_checked_by_the_database(world: Any, admin_engine: Engine) -> None:
    ids = _school_b(world)
    for over, constraint in (
        ({"c": "2027-01-01"}, "portal_calendar_dates_window"),
        ({"s": "rumour"}, "portal_calendar_dates_source_kind_check"),
        ({"k": "Bad Key"}, "portal_calendar_dates_event_key"),
        ({"k": "school:own-1234"}, "portal_calendar_dates_own_label"),  # own events need a label
        ({"l": "A label"}, "portal_calendar_dates_own_label"),  # file events take the file's
        ({"p": "{bseap-ssc-2027}"}, "portal_calendar_dates_profiles"),
    ):
        with pytest.raises(DBAPIError, match=constraint), admin_engine.begin() as c:
            c.execute(text(DATE_INSERT), _date(ids, **over))
    row = _date(ids)
    with admin_engine.begin() as c:
        c.execute(text(DATE_INSERT), row)
    with pytest.raises(DBAPIError, match="portal_calendar_dates_once"), admin_engine.begin() as c:
        c.execute(text(DATE_INSERT), _date(ids, k=row["k"]))
    with admin_engine.begin() as c:
        c.execute(
            text(DATE_INSERT),
            _date(
                ids,
                k=f"school:synthetic-meeting-{uuid.uuid4().hex[:6]}",
                l="Synthetic meeting",
                p="{apaar}",
            ),
        )


def test_FR_RDY_008_reminders_are_unique_and_append_only(world: Any, admin_engine: Engine) -> None:
    ids = _school_b(world)
    insert = (
        "INSERT INTO ops.portal_reminders (id, tenant_id, academic_year_id, event_key, lead_day, "
        "closes_on) VALUES (:i, :t, :y, :k, :d, '2027-02-05')"
    )
    key = f"synthetic_{uuid.uuid4().hex[:8]}"
    params = {"t": ids.tenant_id, "y": ids.year_id, "k": key, "d": 14}
    with admin_engine.begin() as c:
        c.execute(text(insert), {"i": uuid.uuid4(), **params})
    with pytest.raises(DBAPIError, match="portal_reminders_once"), admin_engine.begin() as c:
        c.execute(text(insert), {"i": uuid.uuid4(), **params})
    # Even the table owner cannot rewrite history outside the offboarding purge.
    with pytest.raises(DBAPIError, match="append-only"), admin_engine.begin() as c:
        c.execute(text("UPDATE ops.portal_reminders SET notified = 3 WHERE event_key = :k"), params)
    with pytest.raises(DBAPIError, match="append-only"), admin_engine.begin() as c:
        c.execute(text("DELETE FROM ops.portal_reminders WHERE event_key = :k"), params)


def test_FR_RDY_006_portal_tasks_carry_their_event_key(world: Any, admin_engine: Engine) -> None:
    ids = _school_b(world)
    insert = (
        "INSERT INTO ops.tasks (id, tenant_id, title, owner_membership_id, due_on, source, "
        "portal_event_key, created_by, created_by_membership) VALUES (:i, :t, 'Synthetic', :m, "
        "'2027-02-05', :s, :k, :u, :m)"
    )
    base = {"t": ids.tenant_id, "m": ids.membership_id, "u": ids.user_id}
    with pytest.raises(DBAPIError, match="tasks_portal_event_key"), admin_engine.begin() as c:
        c.execute(text(insert), {"i": uuid.uuid4(), "s": "portal_calendar", "k": None, **base})
    with pytest.raises(DBAPIError, match="tasks_portal_event_key"), admin_engine.begin() as c:
        c.execute(text(insert), {"i": uuid.uuid4(), "s": "manual", "k": "bseap_x", **base})
    with pytest.raises(DBAPIError, match="tasks_circular_has_citation"), admin_engine.begin() as c:
        c.execute(text(insert), {"i": uuid.uuid4(), "s": "circular", "k": None, **base})
    with admin_engine.begin() as c:
        c.execute(
            text(insert),
            {"i": uuid.uuid4(), "s": "portal_calendar", "k": "bseap_ssc_2027_nominal_roll", **base},
        )


# --- populated round trip -------------------------------------------------------------------


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
            ["--tenants", "1", "--code-prefix", "cal"],
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


def test_CLAUDE_6_12_portal_calendar_migration_reversible_with_data(populated: _Walk) -> None:
    admin = populated.admin
    before = ScriptDirectory.from_config(populated.cfg).get_revision(REVISION).down_revision
    assert isinstance(before, str)
    ids = _school(admin)
    task = uuid.uuid4()
    with admin.begin() as c:
        c.execute(text(DATE_INSERT), _date(ids, k="bseap_ssc_2027_nominal_roll"))
        c.execute(
            text(
                "INSERT INTO ops.tasks (id, tenant_id, title, owner_membership_id, due_on, source, "
                "portal_event_key, created_by, created_by_membership) VALUES (:i, :t, "
                "'Synthetic portal task', :m, '2027-02-05', 'portal_calendar', "
                "'bseap_ssc_2027_nominal_roll', :u, :m)"
            ),
            {"i": task, "t": ids.tenant_id, "m": ids.membership_id, "u": ids.user_id},
        )
        c.execute(
            text(
                "INSERT INTO ops.portal_reminders (id, tenant_id, academic_year_id, event_key, "
                "lead_day, closes_on, task_id, notified) VALUES (:i, :t, :y, "
                "'bseap_ssc_2027_nominal_roll', 14, '2027-02-05', :k, 2)"
            ),
            {"i": uuid.uuid4(), "t": ids.tenant_id, "y": ids.year_id, "k": task},
        )
    students = _scalar(admin, "SELECT count(*) FROM sis.students")
    tasks = _scalar(admin, "SELECT count(*) FROM ops.tasks")
    events = _scalar(admin, "SELECT count(*) FROM audit.events")

    command.downgrade(populated.cfg, before)
    for table in TABLES:
        assert _scalar(admin, f"SELECT to_regclass('ops.{table}') IS NULL") is True
    assert _scalar(admin, "SELECT version_num FROM ops.alembic_version") == before
    assert _scalar(admin, "SELECT count(*) FROM sis.students") == students
    assert _scalar(admin, "SELECT count(*) FROM audit.events") == events
    # The owner keeps the work: the portal task is now a manual task.
    assert _scalar(admin, "SELECT count(*) FROM ops.tasks") == tasks
    assert _scalar(admin, "SELECT source FROM ops.tasks WHERE id = :i", i=task) == "manual"
    # The catalog key stays while the seeded school's (synced) roles still hold it.
    # FORCE RLS is back on the tasks table.
    assert _scalar(
        admin, "SELECT relforcerowsecurity FROM pg_class WHERE oid = 'ops.tasks'::regclass"
    )

    command.upgrade(populated.cfg, "head")
    assert _scalar(admin, "SELECT count(*) FROM ops.portal_calendar_dates") == 0
    assert (
        _scalar(
            admin,
            "SELECT count(*) FROM core.permissions WHERE key = :k",
            k="readiness.calendar.manage",
        )
        == 1
    )
    command.downgrade(populated.cfg, before)
    command.upgrade(populated.cfg, "head")
