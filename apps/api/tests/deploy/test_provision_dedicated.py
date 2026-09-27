"""Dedicated-host provisioning command (FR-PLT-003, ADR-0015; docs/16 §13.2).

``python -m app.platform.provision_dedicated`` runs ON a dedicated host against that host's own
database. It creates the one school the control plane registered (same tenant ID), its keys and
system roles, the invited owner, and makes the school live. Everything here is synthetic.
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
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

from app.core import db as core_db
from app.core.config import DeploymentMode, Environment, KeyWrapperKind, Settings
from app.core.crypto import LocalDevKeyWrapper
from app.core.db import context_free_session
from app.platform import provision_dedicated as cli
from app.platform import service as platform_service
from app.tenancy import service as tenancy

pytestmark = pytest.mark.db

MASTER_KEY = SecretStr("synthetic-ci-master-key-0123456789abcdef")
SYSTEM_ROLES = 9


def host_settings(
    tenant_id: uuid.UUID | None, *, mode: DeploymentMode = DeploymentMode.DEDICATED
) -> Settings:
    return Settings(
        env=Environment.CI,
        deployment_mode=mode,
        dedicated_tenant_id=str(tenant_id) if tenant_id else None,
        key_wrapper=KeyWrapperKind.LOCAL_DEV,
        local_dev_master_key=MASTER_KEY,
    )


def argv(tenant_id: uuid.UUID, **overrides: str) -> list[str]:
    values = {
        "--tenant-id": str(tenant_id),
        "--code": "ded-synth-school",
        "--name": "Synthetic Dedicated High School",
        "--boards": "CISCE",
        "--owner-subject": f"synthetic-sub-{tenant_id.hex[:12]}",
        "--owner-name": "Synthetic Owner",
        "--owner-email": "owner@synthetic.example.test",
    }
    values.update(overrides)
    out: list[str] = []
    for flag, value in values.items():
        out += [flag, value]
    return out


@dataclass
class Run:
    code: int
    stdout: str
    stderr: str

    @property
    def fields(self) -> dict[str, str]:
        return dict(line.split("=", 1) for line in self.stdout.splitlines() if "=" in line)


def run(args: list[str], settings: Settings) -> Run:
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(
        args, settings=settings, wrapper=LocalDevKeyWrapper(settings), stdout=out, stderr=err
    )
    return Run(code, out.getvalue(), err.getvalue())


@dataclass
class Host:
    admin: Engine

    def scalar(self, sql: str, **params: Any) -> Any:
        with self.admin.connect() as conn:
            return conn.execute(text(sql), params).scalar_one()

    def column(self, sql: str, **params: Any) -> list[Any]:
        with self.admin.connect() as conn:
            return list(conn.execute(text(sql), params).scalars())


@pytest.fixture
def host(
    test_database: Any,
    make_alembic_config: Callable[[str], Config],
    request: pytest.FixtureRequest,
) -> Iterator[Host]:
    """A fresh, migrated database standing in for one dedicated host (one school per host)."""
    name = "sos_host_" + "".join(c for c in request.node.name.lower() if c.isalnum())[-40:]
    command.upgrade(make_alembic_config(test_database.create_fresh(name)), "head")
    engines = {
        "app": create_engine(test_database.url_for("sos_app", "test-app-pw", name)),
        "platform": create_engine(test_database.url_for("sos_platform", "test-platform-pw", name)),
    }
    admin_url = make_url(test_database.admin_url).set(database=name)
    admin = create_engine(admin_url.render_as_string(hide_password=False))
    saved = {kind: core_db.get_engine(kind) for kind in engines}
    for kind, engine in engines.items():
        core_db.set_engine(kind, engine)
    try:
        yield Host(admin)
    finally:
        for kind, engine in saved.items():
            core_db.set_engine(kind, engine)
        for engine in [*engines.values(), admin]:
            engine.dispose()


def _known_tenants() -> set[uuid.UUID]:
    with context_free_session() as session:
        return set(tenancy.list_tenant_ids(session, None))


# --- refusals (nothing is written) --------------------------------------------------------------


@pytest.mark.usefixtures("app_engine", "platform_engine")
def test_FR_PLT_003_refuses_on_a_shared_deployment() -> None:
    tid = uuid.uuid4()
    result = run(argv(tid), host_settings(tid, mode=DeploymentMode.SHARED))
    assert result.code == cli.EXIT_REFUSED
    assert result.stderr == "refused: not_dedicated\n"
    assert result.stdout == ""
    assert tid not in _known_tenants()


@pytest.mark.usefixtures("app_engine", "platform_engine")
@pytest.mark.parametrize("configured", [None, "other"])
def test_FR_PLT_003_refuses_a_tenant_id_other_than_the_hosts(configured: str | None) -> None:
    tid = uuid.uuid4()
    host_tid = uuid.uuid4() if configured else None
    result = run(argv(tid), host_settings(host_tid))
    assert result.code == cli.EXIT_REFUSED
    assert result.stderr == "refused: tenant_mismatch\n"
    assert tid not in _known_tenants()


@pytest.mark.usefixtures("app_engine", "platform_engine")
@pytest.mark.parametrize(
    "override",
    [
        {"--code": "Not A Code"},
        {"--owner-language": "fr"},
        {"--owner-email": "not-an-email"},
        {"--boards": "NOT-A-BOARD!"},
    ],
)
def test_FR_PLT_003_rejects_invalid_input(override: dict[str, str]) -> None:
    tid = uuid.uuid4()
    result = run(argv(tid, **override), host_settings(tid))
    assert result.code == cli.EXIT_INVALID
    assert tid not in _known_tenants()


def test_FR_PLT_003_rejects_a_malformed_tenant_id() -> None:
    result = run(argv(uuid.uuid4(), **{"--tenant-id": "not-a-uuid"}), host_settings(None))
    assert result.code == cli.EXIT_INVALID


# --- provisioning on a fresh host ---------------------------------------------------------------


def test_FR_PLT_003_provisions_invites_owner_and_activates(host: Host) -> None:
    tid = uuid.uuid4()
    result = run(argv(tid), host_settings(tid))
    assert result.code == cli.EXIT_OK, result.stderr
    fields = result.fields
    assert fields["tenant_id"] == str(tid)
    assert fields["status"] == "active"
    assert fields["owner_invite"] == "created"
    uuid.UUID(fields["owner_membership_id"])
    # IDs and states only: no names or emails are printed (CLAUDE.md §6.5).
    assert "Synthetic" not in result.stdout
    assert "example.test" not in result.stdout

    assert host.scalar("SELECT status FROM core.tenants WHERE id = :t", t=tid) == "active"
    assert host.scalar("SELECT deployment_mode FROM core.tenants WHERE id = :t", t=tid) == (
        "dedicated"
    )
    assert host.scalar("SELECT count(*) FROM core.tenant_keys WHERE tenant_id = :t", t=tid) == 1
    assert host.scalar("SELECT count(*) FROM core.roles WHERE tenant_id = :t", t=tid) == (
        SYSTEM_ROLES
    )
    owner_roles = host.column(
        "SELECT r.key FROM core.memberships m "
        "JOIN core.membership_roles mr ON mr.membership_id = m.id AND mr.tenant_id = m.tenant_id "
        "JOIN core.roles r ON r.id = mr.role_id AND r.tenant_id = mr.tenant_id "
        "WHERE m.tenant_id = :t AND m.status = 'invited' AND m.mfa_required",
        t=tid,
    )
    assert owner_roles == ["owner"]

    platform_events = host.column(
        "SELECT action FROM platform.audit_events "
        "WHERE subject_tenant_id = :t AND actor_type = 'system' ORDER BY seq",
        t=tid,
    )
    assert platform_events == [
        "tenant.provisioned",
        "tenant.owner_invite_created",
        "tenant.activated",
    ]
    school_chain = host.column(
        "SELECT action FROM audit.events WHERE tenant_id = :t ORDER BY seq", t=tid
    )
    assert "tenant.key.created" in school_chain
    assert school_chain[-2:] == ["tenant.provisioned", "tenant.activated"]


def test_FR_PLT_003_rerun_is_idempotent(host: Host) -> None:
    tid = uuid.uuid4()
    settings = host_settings(tid)
    assert run(argv(tid), settings).code == cli.EXIT_OK
    counts_sql = (
        "SELECT (SELECT count(*) FROM core.memberships WHERE tenant_id = :t)"
        " + 100 * (SELECT count(*) FROM core.tenant_keys WHERE tenant_id = :t)"
        " + 10000 * (SELECT count(*) FROM platform.audit_events WHERE subject_tenant_id = :t)"
    )
    before = host.scalar(counts_sql, t=tid)

    again = run(argv(tid), settings)
    assert again.code == cli.EXIT_OK, again.stderr
    assert again.fields["status"] == "active"
    assert again.fields["outcome"] == "already_active"
    assert host.scalar(counts_sql, t=tid) == before


def test_FR_PLT_003_resumes_an_interrupted_run(host: Host, monkeypatch: pytest.MonkeyPatch) -> None:
    tid = uuid.uuid4()
    settings = host_settings(tid)

    def crash(*_: Any, **__: Any) -> Any:
        raise RuntimeError("synthetic interruption")

    with monkeypatch.context() as m:
        m.setattr(platform_service, "invite_school_owner", crash)
        with pytest.raises(RuntimeError):
            run(argv(tid), settings)
    assert host.scalar("SELECT status FROM core.tenants WHERE id = :t", t=tid) == "provisioning"
    assert host.scalar("SELECT count(*) FROM core.memberships WHERE tenant_id = :t", t=tid) == 0

    resumed = run(argv(tid), settings)
    assert resumed.code == cli.EXIT_OK, resumed.stderr
    assert resumed.fields["outcome"] == "resumed"
    assert resumed.fields["owner_invite"] == "created"
    assert host.scalar("SELECT status FROM core.tenants WHERE id = :t", t=tid) == "active"
    assert host.scalar("SELECT count(*) FROM core.tenant_keys WHERE tenant_id = :t", t=tid) == 1
    assert host.column(
        "SELECT action FROM platform.audit_events WHERE subject_tenant_id = :t ORDER BY seq",
        t=tid,
    ) == ["tenant.provisioned", "tenant.owner_invite_created", "tenant.activated"]


def test_FR_PLT_003_one_school_per_host(host: Host) -> None:
    first = uuid.uuid4()
    assert run(argv(first), host_settings(first)).code == cli.EXIT_OK
    second = uuid.uuid4()
    result = run(argv(second, **{"--code": "ded-other-school"}), host_settings(second))
    assert result.code == cli.EXIT_REFUSED
    assert result.stderr == "refused: host_has_other_tenant\n"
    assert host.scalar("SELECT count(*) FROM core.tenants") == 1
