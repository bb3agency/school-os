"""0027_identity_issuer and 0028_profile_scope on a POPULATED synthetic database (ADR-0023,
ADR-0028; CLAUDE.md §6.12, §8).

A fresh database is migrated to head and seeded with a synthetic school, walked down to 0023 and
given a break-glass identity the old way (an operator subject with only a ``platform_support``
membership). Upgrading again must stamp staff identities with the staff issuer, move the
break-glass identity to the operator issuer and swap the three definer functions; walking down
restores the 0003 functions and drops the column without losing a row.
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
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.core import db as core_db
from app.core.config import Environment, KeyWrapperKind, Settings, get_settings
from app.core.crypto import LocalDevKeyWrapper
from app.devtools import seed_synthetic as cli
from app.devtools import seeder

pytestmark = pytest.mark.db
BEFORE = "0023_api_gaps"
DB = "schoolos_identity_issuer_migration"
SETTINGS = Settings(
    env=Environment.CI,
    key_wrapper=KeyWrapperKind.LOCAL_DEV,
    local_dev_master_key=SecretStr("synthetic-ci-master-key-0123456789abcdef"),
)
NEW = (
    "core.resolve_login(text,text,boolean)",
    "core.find_user_id_by_subject(text,text)",
    "core.create_user_for_invite(text,text,public.citext,text,text)",
    "core.user_membership_count(uuid)",
)
OLD = (
    "core.resolve_login(text)",
    "core.find_user_id_by_subject(text)",
    "core.create_user_for_invite(text,text,public.citext,text)",
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
            ["--tenants", "1", "--code-prefix", "iss"],
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


def _functions(admin: Engine, signatures: tuple[str, ...]) -> dict[str, bool]:
    with admin.connect() as c:
        return {
            sig: c.execute(text("SELECT to_regprocedure(:s)"), {"s": sig}).scalar() is not None
            for sig in signatures
        }


def _legacy_breakglass_identity(admin: Engine) -> tuple[uuid.UUID, uuid.UUID]:
    """At 0023: an operator identity with only a platform_support membership (as 0011 made)."""
    uid, mid = uuid.uuid4(), uuid.uuid4()
    with admin.begin() as c:
        tenant: object = c.execute(text("SELECT id FROM core.tenants LIMIT 1")).scalar_one()
        c.execute(
            text(
                "INSERT INTO core.roles (id, tenant_id, key, name_en, name_te, is_system) "
                "VALUES (gen_random_uuid(), :t, 'platform_support', 'SchoolOS support', "
                "'SchoolOS సహాయం', true) ON CONFLICT (tenant_id, key) DO NOTHING"
            ),
            {"t": tenant},
        )
        c.execute(
            text(
                "INSERT INTO core.users (id, idp_subject, display_name) "
                "VALUES (:u, :s, 'Synthetic Support Operator')"
            ),
            {"u": uid, "s": f"op-sub-{uid}"},
        )
        c.execute(
            text(
                "INSERT INTO core.memberships (id, tenant_id, user_id, status, expires_at, "
                "mfa_required) VALUES (:m, :t, :u, 'active', now() + interval '1 hour', true)"
            ),
            {"m": mid, "t": tenant, "u": uid},
        )
        c.execute(
            text(
                "INSERT INTO core.membership_roles (tenant_id, membership_id, role_id) "
                "SELECT :t, :m, r.id FROM core.roles r "
                "WHERE r.tenant_id = :t AND r.key = 'platform_support'"
            ),
            {"t": tenant, "m": mid},
        )
    return uid, mid


def _issuers(admin: Engine) -> dict[uuid.UUID, str | None]:
    with admin.connect() as c:
        return {
            row.id: row.idp_issuer
            for row in c.execute(text("SELECT id, idp_issuer FROM core.users"))
        }


def _user_count(admin: Engine) -> int:
    with admin.connect() as c:
        return int(c.execute(text("SELECT count(*) FROM core.users")).scalar_one())


def test_ADR_0023_expand_backfills_issuers_and_swaps_functions(
    populated: tuple[Config, Engine],
) -> None:
    cfg, admin = populated
    command.downgrade(cfg, BEFORE)
    assert "idp_issuer" not in {c["name"] for c in inspect(admin).get_columns("users", "core")}
    assert all(_functions(admin, OLD).values())
    operator_user, _ = _legacy_breakglass_identity(admin)
    users = _user_count(admin)
    assert users > 2

    command.upgrade(cfg, "head")
    assert all(_functions(admin, NEW).values())
    assert not any(_functions(admin, OLD).values())
    issuers = _issuers(admin)
    settings = get_settings()
    assert issuers.pop(operator_user) == settings.resolved_support_issuer
    assert set(issuers.values()) == {settings.oidc_issuer}, "staff identities: staff issuer"
    column = next(
        c for c in inspect(admin).get_columns("users", "core") if c["name"] == "idp_issuer"
    )
    assert column["nullable"] is True, "NOT NULL is the contract step, a later release"
    with admin.connect() as c:
        unique: set[str] = set(
            c.execute(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = 'core.users'::regclass AND contype = 'u'"
                )
            ).scalars()
        )
    assert {"users_idp_subject_key", "users_idp_issuer_subject_key"} <= unique

    command.downgrade(cfg, BEFORE)
    assert all(_functions(admin, OLD).values())
    assert not any(_functions(admin, NEW).values())
    assert "idp_issuer" not in {c["name"] for c in inspect(admin).get_columns("users", "core")}
    assert _user_count(admin) == users, "no rows lost"
    with admin.connect() as c:
        definer_select: bool = c.execute(
            text("SELECT has_table_privilege('sos_definer', 'core.membership_roles', 'SELECT')")
        ).scalar_one()
    assert definer_select is False, "downgrade revokes the extra grant"

    command.upgrade(cfg, "head")
    assert all(_functions(admin, NEW).values())
