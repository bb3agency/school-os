"""System-role sync for existing schools (ADR-0022; FR-IAM-011, SEC-003, SEC-007, ADR-0021).

``python -m app.identity.sync_system_roles`` brings every school's SYSTEM roles in line with
``app/authz/roles.yaml`` inside each school's own ``tenant_session``. Schools here are synthetic
and are made to look like schools provisioned before ADR-0021 (no ``export.read_all`` /
``export.download_any`` grants). The command runs as ``sos_app``; the admin engine is used only
to arrange and inspect rows.
"""

from __future__ import annotations

import io
import json
import uuid
from collections.abc import Callable, Mapping
from dataclasses import replace
from types import MappingProxyType
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from app.audit import service as audit
from app.authz.catalog import CatalogError, RoleDef, permission_catalog, system_roles
from app.core.config import DeploymentMode, Environment, KeyWrapperKind, Settings
from app.core.db import tenant_session
from app.identity import repository as repo
from app.identity import service as identity
from app.identity import sync_system_roles as cli

pytestmark = pytest.mark.db

ADR_0021_GRANTS = {
    ("owner", "export.read_all"),
    ("owner", "export.download_any"),
    ("principal", "export.read_all"),
    ("office_admin", "export.read_all"),
}
SCHOOL_NAME = "Synthetic Sync High School"


def ci_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "env": Environment.CI,
        "key_wrapper": KeyWrapperKind.LOCAL_DEV,
        "local_dev_master_key": SecretStr("synthetic-ci-master-key-0123456789abcdef"),
    }
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def make_school(admin_engine: Engine, app_engine: Engine) -> Callable[..., uuid.UUID]:
    """A school whose system roles were cloned by an older release (no ADR-0021 grants)."""

    def _make(status: str = "active", *, pre_adr_0021: bool = True) -> uuid.UUID:
        tid = uuid.uuid4()
        with admin_engine.begin() as c:
            c.execute(
                text("INSERT INTO core.tenants (id, code, name, status) VALUES (:i, :c, :n, :s)"),
                {"i": tid, "c": f"t-{uuid.uuid4().hex[:12]}", "n": SCHOOL_NAME, "s": status},
            )
        with tenant_session(tid) as s:
            identity.clone_system_roles(s, tid)
        if pre_adr_0021:
            with admin_engine.begin() as c:
                c.execute(
                    text(
                        "DELETE FROM core.role_permissions WHERE tenant_id = :t "
                        "AND permission_key IN ('export.read_all', 'export.download_any')"
                    ),
                    {"t": tid},
                )
        return tid

    return _make


@pytest.fixture
def only_schools(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """Limit the real eligible-school listing to this test's schools (shared test database)."""
    real = cli.eligible_tenant_ids

    def _limit(*mine: uuid.UUID) -> None:
        def _filtered(*, engine: Engine | None = None) -> list[uuid.UUID]:
            return [t for t in real(engine=engine) if t in set(mine)]

        monkeypatch.setattr(cli, "eligible_tenant_ids", _filtered)

    return _limit


def run_cli(*args: str, settings: Settings | None = None, engine: Engine | None = None) -> Any:
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(
        list(args), settings=settings or ci_settings(), engine=engine, stdout=out, stderr=err
    )
    return code, out.getvalue(), err.getvalue()


def grants(admin_engine: Engine, tid: uuid.UUID) -> set[tuple[str, str]]:
    with admin_engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT r.key, rp.permission_key FROM core.role_permissions rp "
                "JOIN core.roles r ON r.tenant_id = rp.tenant_id AND r.id = rp.role_id "
                "WHERE rp.tenant_id = :t"
            ),
            {"t": tid},
        ).all()
    return {(k, p) for k, p in rows}


def events(admin_engine: Engine, tid: uuid.UUID) -> list[dict[str, Any]]:
    with admin_engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT action, actor_type, actor_id, resource_type, resource_id, summary "
                "FROM audit.events WHERE tenant_id = :t ORDER BY seq"
            ),
            {"t": tid},
        ).all()
    return [dict(r._mapping) for r in rows]


def expected_grants() -> set[tuple[str, str]]:
    from app.authz.catalog import system_roles

    return {(k, p) for k, r in system_roles().items() for p in r.permission_keys}


def add_custom_role(tid: uuid.UUID, key: str, perms: list[str]) -> None:
    with tenant_session(tid) as s:
        role = repo.create_role(s, key=key, name_en="Custom", name_te="Custom", is_system=False)
        for p in perms:
            repo.grant_role_permission(s, role.id, p)


# --- dry run ------------------------------------------------------------------------------


def test_ADR_0022_dry_run_writes_nothing(
    admin_engine: Engine, make_school: Callable[..., uuid.UUID]
) -> None:
    tid = make_school()
    before_grants, before_events = grants(admin_engine, tid), events(admin_engine, tid)

    code, out, err = run_cli("--tenant", str(tid))

    assert code == cli.EXIT_PENDING, err
    assert f"tenant={tid} result=pending" in out
    assert "grants_added=4" in out
    for role, perm in ADR_0021_GRANTS:
        assert f"  + {role} {perm}\n" in out
    assert "summary mode=dry_run" in out
    assert grants(admin_engine, tid) == before_grants
    assert events(admin_engine, tid) == before_events


def test_ADR_0022_dry_run_transaction_is_read_only(
    app_engine: Engine, make_school: Callable[..., uuid.UUID]
) -> None:
    """A dry run cannot write even by mistake: the database refuses writes in its transaction."""
    tid = make_school()
    with tenant_session(tid) as s:
        identity.sync_system_roles(s, tid, apply=False)
        with pytest.raises(DBAPIError, match="read-only"):
            repo.create_role(s, key="sneaky_role", name_en="X", name_te="X")
        s.rollback()


# --- apply --------------------------------------------------------------------------------


def test_ADR_0021_grants_reach_existing_schools_and_custom_roles_are_untouched(
    admin_engine: Engine, make_school: Callable[..., uuid.UUID], only_schools: Any
) -> None:
    a, b = make_school(), make_school()
    add_custom_role(a, "exam_helper", ["student.read_basic"])
    assert not ADR_0021_GRANTS & grants(admin_engine, a)
    only_schools(a, b)

    code, out, err = run_cli("--apply")

    assert code == cli.EXIT_OK, (out, err)
    assert out.count("result=applied") == 2
    assert "summary mode=apply prune=false schools=2" in out
    for tid in (a, b):
        have = grants(admin_engine, tid)
        assert have >= ADR_0021_GRANTS
        system_have = {g for g in have if g[0] != "exam_helper"}
        assert system_have == expected_grants()
    # ADR-0021: not granted beyond owner / principal / office_admin.
    assert ("exam_coordinator", "export.read_all") not in grants(admin_engine, a)
    assert ("principal", "export.download_any") not in grants(admin_engine, a)
    # The custom role got nothing.
    assert {p for r, p in grants(admin_engine, a) if r == "exam_helper"} == {"student.read_basic"}


def test_ADR_0022_second_apply_changes_nothing(
    admin_engine: Engine, make_school: Callable[..., uuid.UUID]
) -> None:
    tid = make_school()
    assert run_cli("--tenant", str(tid), "--apply")[0] == cli.EXIT_OK
    after_first = grants(admin_engine, tid), events(admin_engine, tid)

    code, out, _ = run_cli("--tenant", str(tid), "--apply")

    assert code == cli.EXIT_OK
    assert f"tenant={tid} result=in_line" in out
    assert (grants(admin_engine, tid), events(admin_engine, tid)) == after_first
    # A dry run afterwards also finds nothing to do.
    assert run_cli("--tenant", str(tid))[0] == cli.EXIT_OK


def test_SEC_007_apply_is_audited_in_the_school_chain_without_personal_data(
    admin_engine: Engine, make_school: Callable[..., uuid.UUID]
) -> None:
    tid = make_school()
    before = len(events(admin_engine, tid))

    assert run_cli("--tenant", str(tid), "--apply")[0] == cli.EXIT_OK

    new = events(admin_engine, tid)[before:]
    granted = [e for e in new if e["action"] == "role.permission_granted"]
    assert {(e["summary"]["role_key"], e["summary"]["permission"]) for e in granted} == (
        ADR_0021_GRANTS
    )
    assert all(e["summary"]["via"] == "system_role_sync" for e in granted)
    assert all(e["resource_type"] == "role" and e["resource_id"] for e in granted)
    done = [e for e in new if e["action"] == "role.system_sync_applied"]
    assert len(done) == 1
    assert done[0]["resource_id"] == tid
    assert done[0]["summary"] == {
        "roles_created": 0,
        "grants_added": 4,
        "grants_removed": 0,
        "roles_updated": 0,
        "grants_protected": 0,
        "prune": False,
        "via": "system_role_sync",
    }
    assert len(new) == len(granted) + 1
    assert all(e["actor_type"] == "system" and e["actor_id"] is None for e in new)
    blob = json.dumps([e["summary"] for e in new])
    assert SCHOOL_NAME not in blob
    assert "@" not in blob
    with tenant_session(tid) as s:
        assert audit.verify_chain(s, tid).ok


def test_ADR_0022_missing_system_role_and_names_are_restored(
    admin_engine: Engine, make_school: Callable[..., uuid.UUID]
) -> None:
    tid = make_school(pre_adr_0021=False)
    with admin_engine.begin() as c:
        c.execute(
            text("DELETE FROM core.roles WHERE tenant_id = :t AND key = 'auditor_readonly'"),
            {"t": tid},
        )
        c.execute(
            text(
                "UPDATE core.roles SET name_en = 'Old title' "
                "WHERE tenant_id = :t AND key = 'teacher'"
            ),
            {"t": tid},
        )

    code, out, _ = run_cli("--tenant", str(tid), "--apply")

    assert code == cli.EXIT_OK
    assert "  + role auditor_readonly" in out
    assert "  ~ teacher display names" in out
    assert grants(admin_engine, tid) == expected_grants()
    with admin_engine.connect() as c:
        row = c.execute(
            text("SELECT name_en, is_system FROM core.roles WHERE tenant_id = :t AND key = :k"),
            {"t": tid, "k": "teacher"},
        ).one()
        created: bool = c.execute(
            text(
                "SELECT is_system FROM core.roles WHERE tenant_id = :t AND key = 'auditor_readonly'"
            ),
            {"t": tid},
        ).scalar_one()
    assert tuple(row) == ("Teacher", True)
    assert created is True
    actions = [e["action"] for e in events(admin_engine, tid)]
    assert "role.updated" in actions


# --- isolation and --tenant ---------------------------------------------------------------


def test_ADR_0022_tenant_flag_limits_the_run_to_one_school(
    admin_engine: Engine, make_school: Callable[..., uuid.UUID]
) -> None:
    a, b = make_school(), make_school()
    b_before = grants(admin_engine, b), events(admin_engine, b)

    code, out, _ = run_cli("--tenant", str(a), "--apply")

    assert code == cli.EXIT_OK
    assert str(b) not in out
    assert "schools=1" in out
    assert grants(admin_engine, a) >= ADR_0021_GRANTS
    assert (grants(admin_engine, b), events(admin_engine, b)) == b_before


def test_ADR_0022_a_school_session_cannot_sync_another_school(
    admin_engine: Engine, make_school: Callable[..., uuid.UUID]
) -> None:
    """Per-school isolation: A's tenant_session refuses B's ID, and RLS hides B's roles anyway."""
    a, b = make_school(), make_school()
    b_before = grants(admin_engine, b), events(admin_engine, b)
    with pytest.raises(RuntimeError, match="own tenant_session"), tenant_session(a) as s:
        identity.sync_system_roles(s, b, apply=True)
    with tenant_session(a) as s:
        plan = identity.sync_system_roles(s, a, apply=True)
        assert {r.tenant_id for r in repo.list_roles(s)} == {a}
    assert plan.tenant_id == a
    assert plan.applied
    assert (grants(admin_engine, b), events(admin_engine, b)) == b_before


def test_ADR_0022_statuses_and_unknown_or_offboarding_tenant_refused(
    admin_engine: Engine, make_school: Callable[..., uuid.UUID], only_schools: Any
) -> None:
    active, suspended, provisioning = (
        make_school(),
        make_school("suspended"),
        make_school("provisioning"),
    )
    offboarding = make_school("offboarding")
    only_schools(active, suspended, provisioning, offboarding)

    code, out, _ = run_cli()

    assert code == cli.EXIT_PENDING
    for tid in (active, suspended, provisioning):
        assert f"tenant={tid} result=pending" in out
    assert str(offboarding) not in out
    for tid in (offboarding, uuid.uuid4()):
        code, out, err = run_cli("--tenant", str(tid), "--apply")
        assert code == cli.EXIT_REFUSED
        assert err == "refused: tenant_not_eligible\n"
        assert not out
    assert not ADR_0021_GRANTS & grants(admin_engine, offboarding)


# --- prune --------------------------------------------------------------------------------


def test_ADR_0022_prune_removes_stale_system_grants_only_when_asked(
    admin_engine: Engine, make_school: Callable[..., uuid.UUID]
) -> None:
    tid = make_school(pre_adr_0021=False)
    add_custom_role(tid, "exam_helper", ["student.read_basic", "audit.read"])
    with admin_engine.begin() as c:  # a grant an older roles.yaml gave the teacher role
        c.execute(
            text(
                "INSERT INTO core.role_permissions (tenant_id, role_id, permission_key) "
                "SELECT tenant_id, id, 'audit.read' FROM core.roles "
                "WHERE tenant_id = :t AND key = 'teacher'"
            ),
            {"t": tid},
        )

    code, out, _ = run_cli("--tenant", str(tid))
    assert code == cli.EXIT_OK  # nothing to add; the stale grant is reported and kept
    assert "  = teacher audit.read (not in roles.yaml; kept" in out
    assert run_cli("--tenant", str(tid), "--apply")[0] == cli.EXIT_OK
    assert ("teacher", "audit.read") in grants(admin_engine, tid)

    code, out, _ = run_cli("--tenant", str(tid), "--prune")
    assert code == cli.EXIT_PENDING
    assert "  - teacher audit.read\n" in out
    assert ("teacher", "audit.read") in grants(admin_engine, tid)

    code, out, _ = run_cli("--tenant", str(tid), "--prune", "--apply")
    assert code == cli.EXIT_OK
    assert "grants_removed=1" in out
    have = grants(admin_engine, tid)
    assert ("teacher", "audit.read") not in have
    assert {p for r, p in have if r == "exam_helper"} == {"student.read_basic", "audit.read"}
    assert {g for g in have if g[0] != "exam_helper"} == expected_grants()
    revoked = [e for e in events(admin_engine, tid) if e["action"] == "role.permission_revoked"]
    assert [(e["summary"]["role_key"], e["summary"]["permission"]) for e in revoked] == [
        ("teacher", "audit.read")
    ]
    assert run_cli("--tenant", str(tid), "--prune", "--apply")[0] == cli.EXIT_OK


def test_ADR_0022_custom_role_with_a_system_key_is_a_conflict_and_left_alone(
    admin_engine: Engine, make_school: Callable[..., uuid.UUID]
) -> None:
    tid = make_school()
    with admin_engine.begin() as c:
        c.execute(
            text("UPDATE core.roles SET is_system = false WHERE tenant_id = :t AND key = 'owner'"),
            {"t": tid},
        )
    owner_before = {p for r, p in grants(admin_engine, tid) if r == "owner"}

    code, out, _ = run_cli("--tenant", str(tid), "--apply")

    assert code == cli.EXIT_FAILED
    assert "  ! owner is a custom role in this school; not changed" in out
    assert "conflict=1" in out
    have = grants(admin_engine, tid)
    assert {p for r, p in have if r == "owner"} == owner_before
    assert {("principal", "export.read_all"), ("office_admin", "export.read_all")} <= have


def test_ADR_0022_breakglass_and_unknown_system_roles_are_never_changed(
    admin_engine: Engine, make_school: Callable[..., uuid.UUID]
) -> None:
    tid = make_school(pre_adr_0021=False)
    with tenant_session(tid) as s:
        for key in ("platform_support", "legacy_role"):
            role = repo.create_role(s, key=key, name_en="X", name_te="X", is_system=True)
            repo.grant_role_permission(s, role.id, "student.read_basic")

    code, out, _ = run_cli("--tenant", str(tid), "--prune", "--apply")

    assert code == cli.EXIT_OK
    assert "  ? legacy_role system role not in roles.yaml; not changed" in out
    assert "platform_support" not in out
    have = grants(admin_engine, tid)
    assert {("platform_support", "student.read_basic"), ("legacy_role", "student.read_basic")} <= (
        have
    )


# --- refusals -----------------------------------------------------------------------------


def test_ADR_0022_refuses_roles_that_bypass_rls_or_are_not_sos_app(
    admin_engine: Engine, readonly_engine: Engine, make_school: Callable[..., uuid.UUID]
) -> None:
    tid = make_school()
    before = grants(admin_engine, tid)
    code, _, err = run_cli("--tenant", str(tid), "--apply", engine=admin_engine)
    assert (code, err) == (cli.EXIT_REFUSED, "refused: db_role_bypasses_rls\n")
    code, _, err = run_cli("--tenant", str(tid), "--apply", engine=readonly_engine)
    assert (code, err) == (cli.EXIT_REFUSED, "refused: wrong_db_role\n")
    assert grants(admin_engine, tid) == before


def test_ADR_0022_refuses_until_the_catalog_is_migrated(
    monkeypatch: pytest.MonkeyPatch, make_school: Callable[..., uuid.UUID]
) -> None:
    tid = make_school()
    monkeypatch.setattr(identity, "missing_catalog_permissions", lambda **_: ["export.read_all"])
    code, _, err = run_cli("--tenant", str(tid), "--apply")
    assert (code, err) == (cli.EXIT_REFUSED, "refused: catalog_not_migrated\n")


def test_ADR_0022_catalog_is_migrated_in_the_test_database(app_engine: Engine) -> None:
    assert identity.missing_catalog_permissions() == []


def test_ADR_0022_dedicated_host_handles_only_its_school(
    admin_engine: Engine, make_school: Callable[..., uuid.UUID]
) -> None:
    mine, other = make_school(), make_school()
    host = ci_settings(deployment_mode=DeploymentMode.DEDICATED, dedicated_tenant_id=str(mine))

    code, out, _ = run_cli("--apply", settings=host)
    assert code == cli.EXIT_OK
    assert "schools=1" in out
    assert str(other) not in out
    assert grants(admin_engine, mine) >= ADR_0021_GRANTS
    assert not ADR_0021_GRANTS & grants(admin_engine, other)

    code, _, err = run_cli("--tenant", str(other), settings=host)
    assert (code, err) == (cli.EXIT_REFUSED, "refused: tenant_mismatch\n")
    unset = ci_settings(deployment_mode=DeploymentMode.DEDICATED)
    code, _, err = run_cli(settings=unset)
    assert (code, err) == (cli.EXIT_REFUSED, "refused: dedicated_tenant_id_missing\n")


def test_ADR_0022_invalid_arguments_exit_2() -> None:
    assert run_cli("--tenant", "not-a-uuid")[0] == cli.EXIT_INVALID
    assert run_cli("--bogus")[0] == cli.EXIT_INVALID


def test_ADR_0022_a_failing_school_does_not_stop_the_others(
    admin_engine: Engine,
    make_school: Callable[..., uuid.UUID],
    monkeypatch: pytest.MonkeyPatch,
    only_schools: Any,
) -> None:
    good, bad = make_school(), make_school()
    only_schools(good, bad)
    real = identity.sync_system_roles

    def flaky(session: Any, tenant_id: uuid.UUID, **kw: Any) -> Any:
        if tenant_id == bad:
            raise RuntimeError("synthetic failure")
        return real(session, tenant_id, **kw)

    monkeypatch.setattr(identity, "sync_system_roles", flaky)
    code, out, _ = run_cli("--apply")
    assert code == cli.EXIT_FAILED
    assert f"tenant={bad} result=failed error=RuntimeError" in out
    assert f"tenant={good} result=applied" in out
    assert grants(admin_engine, good) >= ADR_0021_GRANTS
    assert not ADR_0021_GRANTS & grants(admin_engine, bad)


# --- lockout guard: protected grants are never pruned (ADR-0022 amendment 2026-09-27) ---------

LOCKOUT_MINIMUM = {("owner", "role.assign"), ("owner", "user.manage")}


def without_grants(drop: set[tuple[str, str]]) -> Callable[[], Mapping[str, RoleDef]]:
    """A future roles.yaml that no longer lists ``drop`` (the templates, not the school)."""
    real = system_roles()

    def _templates() -> Mapping[str, RoleDef]:
        return MappingProxyType(
            {
                key: replace(
                    role,
                    grants=tuple(g for g in role.grants if (key, g.permission) not in drop),
                )
                for key, role in real.items()
            }
        )

    return _templates


def test_ADR_0022_protected_grants_config_covers_school_administration() -> None:
    protected = identity.protected_system_grants()
    assert protected >= LOCKOUT_MINIMUM
    # Every protected grant is one roles.yaml gives today, so new schools start with it.
    assert protected <= expected_grants()
    catalog = permission_catalog()
    assert all(not catalog[p].is_platform for _, p in protected)


@pytest.mark.parametrize(
    "raw",
    [
        {"version": 2, "protected_grants": {"owner": ["role.assign"]}},
        {"version": 1, "protected_grants": {}},
        {"version": 1, "protected_grants": {"no_such_role": ["role.assign"]}},
        {"version": 1, "protected_grants": {"owner": ["no.such_permission"]}},
        {"version": 1, "protected_grants": {"owner": ["platform.tenants.read"]}},
        {"version": 1, "protected_grants": {"owner": "role.assign"}},
        {"version": 1, "protected_grants": {"owner": []}},
        {"version": 1},
    ],
)
def test_ADR_0022_invalid_protected_grants_config_is_refused(raw: dict[str, Any]) -> None:
    with pytest.raises(CatalogError):
        identity.parse_protected_grants(raw, system_roles(), permission_catalog())


def test_ADR_0022_prune_never_removes_protected_grants(
    admin_engine: Engine,
    make_school: Callable[..., uuid.UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tid = make_school(pre_adr_0021=False)
    # A roles.yaml edit that would take user and role administration away from the owner,
    # next to an ordinary stale grant that pruning may remove.
    monkeypatch.setattr(
        identity, "system_roles", without_grants(LOCKOUT_MINIMUM | {("teacher", "kb.ask")})
    )
    assert ("teacher", "kb.ask") in grants(admin_engine, tid)
    before = len(events(admin_engine, tid))

    code, out, _ = run_cli("--tenant", str(tid))  # no prune: kept and reported as usual
    assert code == cli.EXIT_OK
    assert "  = owner role.assign (not in roles.yaml; kept" in out

    code, out, _ = run_cli("--tenant", str(tid), "--prune")  # dry run
    assert code == cli.EXIT_FAILED
    assert "  ! owner role.assign protected (lockout guard); not removed\n" in out
    assert "  ! owner user.manage protected (lockout guard); not removed\n" in out
    assert "  - teacher kb.ask\n" in out
    assert "  - owner " not in out
    assert "conflict=1" in out

    code, out, _ = run_cli("--tenant", str(tid), "--prune", "--apply")
    assert code == cli.EXIT_FAILED
    assert "grants_removed=1" in out
    assert "grants_protected=2" in out
    have = grants(admin_engine, tid)
    assert have >= LOCKOUT_MINIMUM
    assert ("teacher", "kb.ask") not in have
    new = events(admin_engine, tid)[before:]
    revoked = [
        (e["summary"]["role_key"], e["summary"]["permission"])
        for e in new
        if e["action"] == "role.permission_revoked"
    ]
    assert revoked == [("teacher", "kb.ask")]
    done = [e for e in new if e["action"] == "role.system_sync_applied"]
    assert done[0]["summary"]["grants_protected"] == 2
    assert done[0]["summary"]["grants_removed"] == 1

    # Refused on every later run too: the guard is not a one-off warning.
    code, out, _ = run_cli("--tenant", str(tid), "--prune", "--apply")
    assert code == cli.EXIT_FAILED
    assert grants(admin_engine, tid) >= LOCKOUT_MINIMUM


def test_ADR_0022_protected_grants_are_guarded_only_for_their_role(
    admin_engine: Engine,
    make_school: Callable[..., uuid.UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tid = make_school(pre_adr_0021=False)
    # role.assign is protected for the owner; the principal's copy may be pruned.
    monkeypatch.setattr(identity, "system_roles", without_grants({("principal", "role.assign")}))
    code, out, _ = run_cli("--tenant", str(tid), "--prune", "--apply")
    assert code == cli.EXIT_OK
    assert "grants_protected=0" in out
    have = grants(admin_engine, tid)
    assert ("principal", "role.assign") not in have
    assert ("owner", "role.assign") in have
