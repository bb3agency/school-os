"""0019_export_access: permission catalog for ADR-0021 (CLAUDE.md §6.12, §8; docs/05 §14;
FR-EXP-004, SEC-003, SEC-005).

The revision adds ``export.read_all`` and ``export.download_any`` and makes ``export.board`` /
``export.portal`` step-up permissions. It does not touch role grants (tenant rows under FORCE
RLS). Round trips: a fresh database (no schools: the downgrade removes the new keys) and a
populated one (a synthetic school whose system roles, cloned from ``roles.yaml``, hold the new
keys: the downgrade keeps those keys and every grant, like ``0004_authz_seed``).
"""

from __future__ import annotations

import io
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

from app.authz import catalog
from app.core import db as core_db
from app.core.config import Environment, KeyWrapperKind, Settings
from app.core.crypto import LocalDevKeyWrapper
from app.devtools import seed_synthetic as cli
from app.devtools import seeder

pytestmark = pytest.mark.db
REVISION = "0019_export_access"
PREVIOUS = "0018_extraction_redaction"
NEW = ("export.download_any", "export.read_all")
SETTINGS = Settings(
    env=Environment.CI,
    key_wrapper=KeyWrapperKind.LOCAL_DEV,
    local_dev_master_key=SecretStr("synthetic-ci-master-key-0123456789abcdef"),
)


def _admin(test_database: Any, db: str) -> Engine:
    url = make_url(test_database.admin_url).set(database=db)
    return create_engine(url.render_as_string(hide_password=False))


def _flags(admin: Engine) -> dict[str, bool]:
    with admin.connect() as c:
        rows = c.execute(
            text(
                "SELECT key, step_up FROM core.permissions WHERE key IN "
                "('export.board', 'export.portal', 'export.read_all', 'export.download_any')"
            )
        )
        return {r.key: r.step_up for r in rows}


def _later_keys(cfg: Config) -> frozenset[str]:
    """Permission keys that revisions after :data:`REVISION` write out themselves
    (``NEW_PERMISSIONS``, like this one) and delete again on downgrade when no role holds them
    (``0033_certificates``, ``0034_circulars``): absent at :data:`REVISION` on a fresh database."""
    script = ScriptDirectory.from_config(cfg)
    keys: set[str] = set()
    for rev in script.walk_revisions(base=REVISION, head="heads"):
        if rev.revision == REVISION:
            continue
        for row in getattr(rev.module, "NEW_PERMISSIONS", ()):
            keys.add(str(row["key"]))
    return frozenset(keys)


def _catalog_matches(admin: Engine, *, without: frozenset[str] = frozenset()) -> bool:
    """``core.permissions`` is exactly ``permissions.yaml`` minus ``without`` (keys owned by
    later revisions, which must then be absent)."""
    with admin.connect() as c:
        rows = c.execute(
            text("SELECT key, description, sensitivity, step_up, is_platform FROM core.permissions")
        ).all()
    db = {r.key: (r.description, r.sensitivity, r.step_up, r.is_platform) for r in rows}
    expected = {
        k: (p.description, p.sensitivity, p.step_up, p.is_platform)
        for k, p in catalog.permission_catalog().items()
        if k not in without
    }
    return db == expected


def _grants(admin: Engine) -> list[tuple[str, str]]:
    """(role key, permission) for the new permissions, across schools (admin bypasses RLS)."""
    with admin.connect() as c:
        rows = c.execute(
            text(
                "SELECT r.key, rp.permission_key FROM core.role_permissions rp "
                "JOIN core.roles r ON r.tenant_id = rp.tenant_id AND r.id = rp.role_id "
                "WHERE rp.permission_key IN ('export.read_all', 'export.download_any') "
                "ORDER BY 1, 2"
            )
        )
        return [(r[0], r[1]) for r in rows]


def test_CLAUDE_6_12_export_access_round_trip_fresh(
    test_database: Any, make_alembic_config: Callable[[str], Config]
) -> None:
    db = "schoolos_export_access_fresh"
    cfg = make_alembic_config(test_database.create_fresh(db))
    command.upgrade(cfg, "head")
    admin = _admin(test_database, db)
    try:
        assert _catalog_matches(admin)
        assert _flags(admin) == {
            "export.board": True,
            "export.portal": True,
            "export.read_all": False,
            "export.download_any": True,
        }
        command.downgrade(cfg, PREVIOUS)
        assert _flags(admin) == {"export.board": False, "export.portal": False}
        command.upgrade(cfg, REVISION)
        later = _later_keys(cfg)
        assert {"certificate.approve", "task.manage"} <= later
        assert not later & set(NEW)
        assert _catalog_matches(admin, without=later)
        command.downgrade(cfg, PREVIOUS)
        command.upgrade(cfg, "head")
        assert _catalog_matches(admin)
    finally:
        admin.dispose()


@pytest.fixture
def populated(
    test_database: Any, make_alembic_config: Callable[[str], Config]
) -> Iterator[tuple[Config, Engine]]:
    db = "schoolos_export_access_populated"
    cfg = make_alembic_config(test_database.create_fresh(db))
    command.upgrade(cfg, "head")
    engines = {
        "app": create_engine(test_database.url_for("sos_app", "test-app-pw", db)),
        "platform": create_engine(test_database.url_for("sos_platform", "test-platform-pw", db)),
        "admin": _admin(test_database, db),
    }
    saved = {k: core_db.get_engine(k) for k in ("app", "platform")}
    core_db.set_engine("app", engines["app"])
    core_db.set_engine("platform", engines["platform"])
    try:
        code = cli.main(
            ["--tenants", "1", "--code-prefix", "exacc"],
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


def test_CLAUDE_6_12_export_access_round_trip_populated(
    populated: tuple[Config, Engine],
) -> None:
    cfg, admin = populated
    # Provisioning cloned the system roles from roles.yaml (ADR-0021 decision 3).
    grants = _grants(admin)
    assert grants == [
        ("office_admin", "export.read_all"),
        ("owner", "export.download_any"),
        ("owner", "export.read_all"),
        ("principal", "export.read_all"),
    ]
    command.downgrade(cfg, PREVIOUS)
    flags = _flags(admin)
    assert (flags["export.board"], flags["export.portal"]) == (False, False)
    # Keys still granted to roles are kept (FK), and no grant is touched.
    assert set(flags) >= set(NEW)
    assert _grants(admin) == grants
    command.upgrade(cfg, "head")
    assert _catalog_matches(admin)
    assert _grants(admin) == grants
