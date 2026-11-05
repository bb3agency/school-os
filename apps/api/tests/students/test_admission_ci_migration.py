"""0073_admission_no_ci on a populated database (D53; BR-01, FR-STU-001; CLAUDE.md §6.12).

Two synthetic schools are seeded (``seed-synthetic``) and given students with admission numbers.
The walk 0073 -> 0072 -> 0073 must succeed with that data present. With case-variant duplicates
in place the upgrade stops, names only counts (schools, duplicate groups; never a number or a
school), and leaves the database at 0072; once the school resolves them it runs.
"""

from __future__ import annotations

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
DB = "schoolos_admission_ci_migration"
REV = "0073_admission_no_ci"
BELOW = "0072_outbox_retention"
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
            ["--tenants", "2", "--code-prefix", "admci"],
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


def _tenants(admin: Engine) -> list[uuid.UUID]:
    with admin.connect() as c:
        rows: list[Any] = list(
            c.execute(text("SELECT id FROM core.tenants ORDER BY created_at, id")).scalars()
        )
        return [uuid.UUID(str(t)) for t in rows]


def _add(admin: Engine, tenant_id: uuid.UUID, *numbers: str) -> list[uuid.UUID]:
    """Students with only an admission number (the projection column the index covers)."""
    ids = [uuid.uuid4() for _ in numbers]
    with admin.begin() as c:
        for sid, number in zip(ids, numbers, strict=True):
            c.execute(
                text(
                    "INSERT INTO sis.students (id, tenant_id, status, admission_no) "
                    "VALUES (:i, :t, 'active', :n)"
                ),
                {"i": sid, "t": tenant_id, "n": number},
            )
    return ids


def _scalar(admin: Engine, sql: str) -> Any:
    with admin.connect() as c:
        return c.execute(text(sql)).scalar_one()


INDEX = "SELECT to_regclass('sis.students_adm_no_ci') IS NOT NULL"


def test_D53_admission_no_ci_round_trips_on_a_populated_database(
    populated: tuple[Config, Engine],
) -> None:
    cfg, admin = populated
    a, b = _tenants(admin)[:2]
    _add(admin, a, "ADM/1", "ADM/2")
    _add(admin, b, "adm/1")  # another school may use any spelling
    assert _scalar(admin, INDEX) is True
    command.downgrade(cfg, BELOW)
    assert _scalar(admin, INDEX) is False
    # The exact index stays (expand only).
    assert _scalar(admin, "SELECT to_regclass('sis.students_adm_no') IS NOT NULL") is True
    command.upgrade(cfg, REV)
    command.downgrade(cfg, BELOW)
    command.upgrade(cfg, "head")
    assert _scalar(admin, INDEX) is True
    assert _scalar(admin, "SELECT count(*) FROM sis.students WHERE admission_no IS NOT NULL") >= 3
    # RLS stays forced on the table after the owner's read-only count.
    assert (
        _scalar(
            admin, "SELECT relforcerowsecurity FROM pg_class WHERE oid = 'sis.students'::regclass"
        )
        is True
    )
    # The index now refuses a case variant within one school.
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError, match="students_adm_no_ci"):
        _add(admin, a, "adm/2")


def test_D53_admission_no_ci_stops_on_case_duplicates_with_counts_only(
    populated: tuple[Config, Engine],
) -> None:
    cfg, admin = populated
    a, b = _tenants(admin)[:2]
    command.downgrade(cfg, BELOW)
    # School A: two groups (one of three spellings); school B: one group.
    dupes = _add(admin, a, "SYNCASE/1", "syncase/1", "SynCase/1", "SYNCASE/2", "syncase/2") + _add(
        admin, b, "SYNCASE/9", "syncase/9"
    )
    _add(admin, b, "SYNCASE/1")  # same number in another school: not a duplicate

    with pytest.raises(RuntimeError, match="differ only in case") as exc:
        command.upgrade(cfg, REV)
    message = str(exc.value)
    assert "2 school(s), 3 duplicate group(s)" in message
    assert "syncase" not in message.lower()  # no values
    for tenant in (a, b):
        assert str(tenant) not in message  # no school ids
    assert _scalar(admin, "SELECT version_num FROM ops.alembic_version") == BELOW
    assert _scalar(admin, INDEX) is False
    assert (
        _scalar(
            admin, "SELECT relforcerowsecurity FROM pg_class WHERE oid = 'sis.students'::regclass"
        )
        is True
    )

    # Once the schools resolve them (here: each variant gets its own number) it runs.
    with admin.begin() as c:
        for n, sid in enumerate((dupes[1], dupes[2], dupes[4], dupes[6])):
            c.execute(
                text("UPDATE sis.students SET admission_no = :n WHERE id = :i"),
                {"n": f"SYNFIX/{n}", "i": sid},
            )
    command.upgrade(cfg, "head")
    assert _scalar(admin, INDEX) is True
