"""0020_provisioning_runs: table, grants, backfill and round trip (FR-PLT-002; CLAUDE.md §6.1,
§6.12; docs/16 §5.4).

Runs on its own freshly bootstrapped database. Schools that existed before the revision get a
run: dedicated and fully provisioned shared schools ``completed``, an unfinished shared school
``registered`` without a fingerprint. All data is synthetic.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.audit import service as audit

from .conftest import letters

pytestmark = pytest.mark.db
REVISION = "0020_provisioning_runs"
PREVIOUS = "0019_export_access"
DB = "schoolos_provisioning_runs"


@pytest.fixture
def fresh(
    test_database: Any, make_alembic_config: Callable[[str], Config]
) -> Iterator[tuple[Config, Engine, Engine, Engine]]:
    cfg = make_alembic_config(test_database.create_fresh(DB))
    command.upgrade(cfg, PREVIOUS)
    admin_url = make_url(test_database.admin_url).set(database=DB)
    admin = create_engine(admin_url.render_as_string(hide_password=False))
    platform = create_engine(test_database.url_for("sos_platform", "test-platform-pw", DB))
    app = create_engine(test_database.url_for("sos_app", "test-app-pw", DB))
    try:
        yield cfg, admin, platform, app
    finally:
        for engine in (admin, platform, app):
            engine.dispose()


def _deployment(platform: Engine, mode: str) -> tuple[uuid.UUID, str]:
    tid, code = uuid.uuid4(), f"s-{letters(12)}"
    with platform.begin() as c:
        c.execute(
            text(
                "INSERT INTO platform.deployments (id, tenant_id, tenant_code, school_name, mode, "
                "tenant_status, status, heartbeat_key_id, heartbeat_key_ciphertext) VALUES "
                "(:i, :t, :c, 'Synthetic School', :m, 'provisioning', :st, :k, :kc)"
            ),
            {
                "i": uuid.uuid4(),
                "t": tid,
                "c": code,
                "m": mode,
                "st": "healthy" if mode == "shared" else "provisioning",
                "k": "hb-synthetic" if mode == "dedicated" else None,
                "kc": b"\x01" * 16 if mode == "dedicated" else None,
            },
        )
    return tid, code


def _runs(admin: Engine) -> dict[uuid.UUID, tuple[str, str, bool]]:
    with admin.connect() as c:
        rows = c.execute(
            text(
                "SELECT tenant_id, tier, state, request_sha256 IS NULL AS legacy "
                "FROM platform.provisioning_runs"
            )
        )
        return {r.tenant_id: (r.tier, r.state, r.legacy) for r in rows}


def _table_exists(admin: Engine) -> bool:
    with admin.connect() as c:
        return bool(c.execute(text("SELECT to_regclass('platform.provisioning_runs')")).scalar())


def test_FR_PLT_002_backfill_grants_and_round_trip(
    fresh: tuple[Config, Engine, Engine, Engine],
) -> None:
    cfg, admin, platform, app = fresh
    dedicated, _ = _deployment(platform, "dedicated")
    finished, _ = _deployment(platform, "shared")
    unfinished, _ = _deployment(platform, "shared")
    with Session(platform) as s, s.begin():
        audit.record_platform(
            s,
            action="tenant.owner_invite_created",
            resource_type="membership",
            resource_id=uuid.uuid4(),
            summary={"owner_role_assigned": True},
            actor_type="system",
            subject_tenant_id=finished,
        )

    command.upgrade(cfg, REVISION)
    assert _runs(admin) == {
        dedicated: ("dedicated", "completed", True),
        finished: ("shared", "completed", True),
        unfinished: ("shared", "registered", True),
    }

    # Grants: the control plane reads and writes runs but never deletes; nobody else sees them.
    with platform.begin() as c:
        c.execute(
            text("UPDATE platform.provisioning_runs SET attempts = 2 WHERE tenant_id = :t"),
            {"t": unfinished},
        )
    with pytest.raises(DBAPIError, match="permission denied"), platform.begin() as c:
        c.execute(text("DELETE FROM platform.provisioning_runs"))
    with pytest.raises(DBAPIError, match="permission denied"), app.begin() as c:
        c.execute(text("SELECT count(*) FROM platform.provisioning_runs"))

    # CHECKs: a completed run holds no owner parameters or lease; a failure names its step.
    for sql in (
        "UPDATE platform.provisioning_runs SET owner_subject = 'owner-synthetic' "
        "WHERE tenant_id = :t",
        "UPDATE platform.provisioning_runs SET state = 'failed' WHERE tenant_id = :t",
    ):
        with pytest.raises(DBAPIError, match="violates check constraint"), platform.begin() as c:
            c.execute(text(sql), {"t": finished})
    with pytest.raises(DBAPIError, match="violates check constraint"), platform.begin() as c:
        c.execute(
            text(
                "UPDATE platform.provisioning_runs SET owner_subject = 'owner-synthetic' "
                "WHERE tenant_id = :t"
            ),
            {"t": dedicated},
        )

    command.downgrade(cfg, PREVIOUS)
    assert not _table_exists(admin)
    command.upgrade(cfg, "head")
    assert set(_runs(admin)) == {dedicated, finished, unfinished}
