"""0008_sis_students on a populated database (CLAUDE.md §6.12, §8; docs/05 §14).

A fresh database is migrated to head and seeded with a synthetic school (``seed-synthetic``),
then students with encrypted values, guardians and enrolments are added through the service.
The walk 0008 -> 0007 -> 0008 -> 0007 -> head must succeed with that data present, and the
global attribute catalog must be re-seeded (deterministic ids, read-only for the app).
"""

from __future__ import annotations

import io
import sys
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
from app.core.db import tenant_session
from app.devtools import seed_synthetic as cli
from app.devtools import seeder
from app.students import service as students
from app.students.schemas import GuardianCreate, StudentCreate, ValueIn

pytestmark = pytest.mark.db
SW = sys.modules["sos_test_student_world"]
DB = "schoolos_students_migration"
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
            ["--tenants", "1", "--code-prefix", "stum"],
            settings=SETTINGS,
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            wrapper=LocalDevKeyWrapper(SETTINGS),
            owner_bootstrap=seeder.PlatformOwnerBootstrap(),
        )
        assert code == cli.EXIT_OK
        _add_students(engines["admin"])
        yield cfg, engines["admin"]
    finally:
        for kind, engine in saved.items():
            core_db.set_engine(kind, engine)
        for engine in engines.values():
            engine.dispose()


def _add_students(admin: Engine) -> None:
    SW.configure_keyring()
    with admin.connect() as c:
        row = c.execute(
            text(
                "SELECT m.tenant_id, m.user_id, m.id AS membership_id, s.id AS section_id "
                "FROM core.memberships m JOIN core.sections s ON s.tenant_id = m.tenant_id "
                "JOIN core.academic_years y ON y.id = s.academic_year_id AND y.is_current "
                "WHERE m.status = 'active' ORDER BY m.created_at LIMIT 1"
            )
        ).one()
    person = SW.W.Person("office_admin", row.user_id, row.membership_id, "sub", "Synthetic")
    ctx = SW.ctx_for(row.tenant_id, person, "office_admin")
    with tenant_session(row.tenant_id, row.user_id) as db:
        for i in range(3):
            out = students.create_student(
                db,
                ctx,
                StudentCreate(
                    values=[
                        ValueIn(
                            attribute_key="full_name",
                            source="admission_register",
                            value=f"Synthetica Walk {i}",
                        ),
                        ValueIn(
                            attribute_key="health_notes",
                            source="parent_form",
                            value="Synthetic note",
                        ),
                    ],
                    section_id=row.section_id,
                ),
            )
            students.record_value(db, ctx, out.id, "mother_tongue", "parent_form", "Telugu")
            students.record_value(db, ctx, out.id, "mother_tongue", "parent_form", "Hindi")
            students.add_guardian(
                db,
                ctx,
                out.id,
                GuardianCreate(
                    relationship="mother", full_name="Synthetica Mother", phone="9876500000"
                ),
            )


def _scalar(admin: Engine, sql: str) -> Any:
    with admin.connect() as c:
        return c.execute(text(sql)).scalar_one()


def test_CLAUDE_6_12_sis_migration_reversible_with_data(populated: tuple[Config, Engine]) -> None:
    cfg, admin = populated
    assert _scalar(admin, "SELECT count(*) FROM sis.attribute_values") >= 12
    ids = _scalar(admin, "SELECT array_agg(id ORDER BY key) FROM sis.attribute_definitions")
    command.downgrade(cfg, "0007_accept_invitations")
    assert _scalar(admin, "SELECT to_regclass('sis.students') IS NULL") is True
    assert _scalar(admin, "SELECT count(*) FROM core.memberships") > 0, "earlier data untouched"
    command.upgrade(cfg, "0008_sis_students")
    # 0008 seeds its own keys; later revisions seed the keys they add (0042_apaar_id).
    assert _scalar(
        admin,
        "SELECT array_agg(id ORDER BY key) FROM sis.attribute_definitions "
        "WHERE key <> ALL (ARRAY['apaar_id', 'udise_pen'])",
    ) == _scalar(
        admin,
        "SELECT array_agg(id ORDER BY key) FROM sis.attribute_definitions",
    )
    assert len(ids) - 2 == _scalar(admin, "SELECT count(*) FROM sis.attribute_definitions")
    command.downgrade(cfg, "0007_accept_invitations")
    command.upgrade(cfg, "head")
    assert _scalar(
        admin, "SELECT count(*) FROM sis.attribute_definitions WHERE tenant_id IS NULL"
    ) == len(ids)
    assert all(isinstance(i, uuid.UUID) for i in ids)


APAAR = "0042_apaar_id"
BEFORE_APAAR = "0040_contextual_retrieval"


def _apaar_student(admin: Engine) -> uuid.UUID:
    """A synthetic student with an APAAR ID and a PEN recorded through the service."""
    with admin.connect() as c:
        row = c.execute(
            text(
                "SELECT m.tenant_id, m.user_id, m.id AS membership_id FROM core.memberships m "
                "WHERE m.status = 'active' ORDER BY m.created_at LIMIT 1"
            )
        ).one()
        sid = c.execute(
            text("SELECT id FROM sis.students WHERE tenant_id = :t ORDER BY id LIMIT 1"),
            {"t": row.tenant_id},
        ).scalar_one()
    person = SW.W.Person("office_admin", row.user_id, row.membership_id, "sub", "Synthetic")
    ctx = SW.ctx_for(row.tenant_id, person, "office_admin")
    with tenant_session(row.tenant_id, row.user_id) as db:
        students.record_value(db, ctx, sid, "apaar_id", "udise_plus", "1234 5678 9011")
        students.record_value(db, ctx, sid, "udise_pen", "udise_plus", "21345678901")
    return uuid.UUID(str(sid))


def test_FR_STU_013_apaar_migration_reversible_with_values(
    populated: tuple[Config, Engine],
) -> None:
    """0042_apaar_id: down keeps recorded values and drops the two global definitions and the
    ``digits12`` type; up re-seeds the same ids and the values show again (invariant 12)."""
    cfg, admin = populated
    sid = _apaar_student(admin)
    keys = "SELECT array_agg(key ORDER BY key) FROM sis.attribute_definitions WHERE key IN "         "('apaar_id', 'udise_pen')"
    ids = _scalar(admin, keys.replace("array_agg(key", "array_agg(id"))
    assert _scalar(admin, keys) == ["apaar_id", "udise_pen"]
    assert _scalar(
        admin, "SELECT data_type FROM sis.attribute_definitions WHERE key = 'apaar_id'"
    ) == "digits12"
    values = (
        f"SELECT count(*) FROM sis.attribute_values WHERE student_id = '{sid}' "
        "AND attribute_key IN ('apaar_id', 'udise_pen')"
    )
    assert _scalar(admin, values) == 2
    assert _scalar(
        admin,
        f"SELECT value_text FROM sis.attribute_values WHERE student_id = '{sid}' "
        "AND attribute_key = 'apaar_id'",
    ) == "123456789011"

    command.downgrade(cfg, BEFORE_APAAR)
    assert _scalar(admin, keys) is None
    assert _scalar(admin, values) == 2, "recorded values are kept"
    with pytest.raises(Exception, match="attribute_definitions_data_type_check"):  # noqa: PT011
        with admin.begin() as c:
            c.execute(
                text(
                    "INSERT INTO sis.attribute_definitions (id, key, data_type, classification, "
                    "canonical_policy, label_en, label_te) VALUES (gen_random_uuid(), 'x_digits', "
                    "'digits12', 'C2', '{}'::jsonb, 'X', 'X')"
                )
            )

    command.upgrade(cfg, "head")
    assert _scalar(admin, keys.replace("array_agg(key", "array_agg(id")) == ids
    assert _scalar(admin, values) == 2


def test_FR_STU_015_digits12_is_only_for_global_definitions(
    populated: tuple[Config, Engine],
) -> None:
    """A school's own attribute can never be ``digits12`` (the typed-field exemption)."""
    _, admin = populated
    tenant = _scalar(admin, "SELECT id FROM core.tenants ORDER BY created_at LIMIT 1")
    with pytest.raises(Exception, match="attribute_definitions_digits12_global"):  # noqa: PT011
        with admin.begin() as c:
            c.execute(
                text(
                    "INSERT INTO sis.attribute_definitions (id, tenant_id, key, data_type, "
                    "classification, canonical_policy, label_en, label_te) VALUES "
                    "(gen_random_uuid(), :t, 'school_id12', 'digits12', 'C2', '{}'::jsonb, "
                    "'X', 'X')"
                ),
                {"t": tenant},
            )
