"""authz runtime pieces: KV store, snapshot cache, scope reach, resolver rules and the
system-role cloning hook (FR-IAM-011, FR-IAM-012, FR-IAM-014, docs/04 §10)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
import redis
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from app.authz import cache
from app.authz.cache import PermissionSnapshot
from app.authz.context import Scopes, UserContext
from app.authz.kv import InMemoryKV, KVUnavailable, RedisKV, set_kv_store
from app.authz.resolver import AuthzResolver, build_snapshot
from app.core.db import tenant_session
from app.core.errors import Conflict, Forbidden
from app.identity import service as identity
from app.identity.schemas import LoginChoice, MembershipAccess, RoleAccess

T1, M1 = uuid.uuid4(), uuid.uuid4()


@pytest.fixture
def store() -> Iterator[InMemoryKV]:
    clock = [100.0]
    kv = InMemoryKV(max_entries=10, clock=lambda: clock[0])
    kv.clock_ref = clock  # type: ignore[attr-defined]
    set_kv_store(kv)
    yield kv
    set_kv_store(None)


def test_kv_ttl_nx_incr_and_bound(store: InMemoryKV) -> None:
    clock: list[float] = store.clock_ref  # type: ignore[attr-defined]
    assert store.set("sos:a", b"1", ttl_s=10)
    assert not store.set("sos:a", b"2", ttl_s=10, nx=True)
    assert store.get("sos:a") == b"1"
    assert store.incr("sos:n", ttl_s=5) == 1
    assert store.incr("sos:n", ttl_s=5) == 2
    clock[0] += 11
    assert store.get("sos:a") is None
    assert store.incr("sos:n", ttl_s=5) == 1
    for i in range(9):
        store.set(f"sos:k{i}", b"x", ttl_s=100)
    with pytest.raises(KVUnavailable):
        store.set("sos:overflow", b"x", ttl_s=100)


class _Broken:
    def __getattr__(self, name: str) -> Any:
        def fail(*args: Any, **kwargs: Any) -> Any:
            raise redis.ConnectionError("synthetic outage")

        return fail


def test_redis_kv_maps_errors_to_unavailable() -> None:
    kv = RedisKV(_Broken())
    for call in (
        lambda: kv.get("k"),
        lambda: kv.set("k", b"v", ttl_s=1),
        lambda: kv.delete("k"),
        lambda: kv.incr("k", ttl_s=1),
    ):
        with pytest.raises(KVUnavailable):
            call()


def _snap() -> PermissionSnapshot:
    return PermissionSnapshot(
        roles=frozenset({"class_teacher"}),
        permissions=frozenset({"student.read_basic", "session.authenticated"}),
        scoped_permissions=frozenset({"student.read_basic"}),
        scopes=Scopes(section_ids=frozenset({uuid.uuid4()})),
        mfa_required=False,
    )


def test_FR_IAM_014_snapshot_roundtrip_ttl_and_generation(store: InMemoryKV) -> None:
    clock: list[float] = store.clock_ref  # type: ignore[attr-defined]
    snap = _snap()
    assert PermissionSnapshot.from_json(snap.to_json()) == snap
    cache.put_snapshot(T1, M1, snap)
    assert cache.get_snapshot(T1, M1) == snap
    clock[0] += cache.SNAPSHOT_TTL_S + 1
    assert cache.get_snapshot(T1, M1) is None
    cache.put_snapshot(T1, M1, snap)
    cache.invalidate_tenant(T1)
    assert cache.get_snapshot(T1, M1) is None, "tenant generation bump drops every snapshot"
    cache.put_snapshot(T1, M1, snap)
    cache.invalidate_membership(T1, M1)
    assert cache.get_snapshot(T1, M1) is None


def test_docs_04_cache_keys_include_tenant(store: InMemoryKV) -> None:
    cache.put_snapshot(T1, M1, _snap())
    other = uuid.uuid4()
    assert cache.get_snapshot(other, M1) is None
    assert all(str(T1) in key for key in store._data)


def test_cache_outage_is_not_fatal() -> None:
    set_kv_store(RedisKV(_Broken()))
    try:
        cache.put_snapshot(T1, M1, _snap())
        assert cache.get_snapshot(T1, M1) is None
        cache.invalidate_membership(T1, M1)
        cache.invalidate_tenant(T1)
    finally:
        set_kv_store(None)


def _invalidate_then_fail(tid: uuid.UUID) -> None:
    with tenant_session(tid) as s:
        cache.invalidate_on_commit(s, tid, M1)
        raise RuntimeError("rolled back")


@pytest.mark.db
def test_FR_IAM_014_invalidation_happens_after_commit_only(
    store: InMemoryKV, app_engine: Engine
) -> None:
    tid = uuid.uuid4()
    cache.put_snapshot(tid, M1, _snap())
    with pytest.raises(RuntimeError):
        _invalidate_then_fail(tid)
    assert cache.get_snapshot(tid, M1) is not None, "a rollback keeps the snapshot"
    with tenant_session(tid) as s:
        cache.invalidate_on_commit(s, tid, M1)
        assert cache.get_snapshot(tid, M1) is not None, "not before the commit"
    assert cache.get_snapshot(tid, M1) is None


def _ctx(**overrides: Any) -> UserContext:
    values: dict[str, Any] = {
        "user_id": uuid.uuid4(),
        "tenant_id": T1,
        "membership_id": M1,
        "roles": frozenset({"class_teacher"}),
        "permissions": frozenset({"student.read_basic", "support.ticket.create"}),
        "scopes": Scopes(section_ids=frozenset({M1})),
        "mfa": False,
        "auth_time": None,
        "scoped_permissions": frozenset({"student.read_basic"}),
    }
    values.update(overrides)
    return UserContext(**values)


def test_FR_IAM_012_scope_reach() -> None:
    ctx = _ctx()
    grant = ctx.scope_for("student.read_basic")
    assert not grant.school_wide
    assert grant.section_ids == {M1}
    assert ctx.scope_for("support.ticket.create").school_wide
    assert not ctx.scope_for("audit.read").school_wide
    school = _ctx(scopes=Scopes(school=True))
    assert school.scope_for("student.read_basic").school_wide


def test_FR_IAM_011_snapshot_marks_scoped_only_when_no_role_grants_school_wide() -> None:
    access = MembershipAccess(
        roles=(
            RoleAccess("class_teacher", True, frozenset({"student.read_basic", "kb.ask"})),
            RoleAccess("office_staff", True, frozenset({"student.read_basic"})),
            RoleAccess("custom_helper", False, frozenset({"document.read"})),
        ),
        scopes=(("section", M1), ("class", T1)),
        mfa_required=False,
    )
    snap = build_snapshot(access)
    assert "session.authenticated" in snap.permissions
    assert "student.read_basic" not in snap.scoped_permissions  # office_staff grants it ✓
    assert {"kb.ask", "document.read"} <= snap.scoped_permissions  # S and custom role
    assert snap.scopes == Scopes(class_ids=frozenset({T1}), section_ids=frozenset({M1}))
    assert not snap.mfa_required
    privileged = build_snapshot(
        MembershipAccess(
            roles=(RoleAccess("principal", True, frozenset()),), scopes=(), mfa_required=False
        )
    )
    assert privileged.mfa_required


def test_FR_IAM_013_resolver_chooses_tenant() -> None:
    r = AuthzResolver()
    a = LoginChoice(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), "active")
    b = LoginChoice(a.user_id, uuid.uuid4(), uuid.uuid4(), "active")
    assert r.choose([a], None) == a
    assert r.choose([a, b], b.tenant_id) == b
    with pytest.raises(Conflict):
        r.choose([a, b], None)
    with pytest.raises(Forbidden):
        r.choose([a], uuid.uuid4())
    with pytest.raises(Forbidden):
        r.choose([], None)


@pytest.mark.db
def test_FR_IAM_011_clone_system_roles_is_idempotent(
    admin_engine: Engine, app_engine: Engine, platform_engine: Engine
) -> None:
    from app.tenancy import service as tenancy

    tid = tenancy.provision_tenant(
        code=f"s-{uuid.uuid4().hex[:12]}",
        name="Synthetic Clone School",
        wrapper=_wrapper(),
    ).tenant_id
    with admin_engine.connect() as c:
        roles = c.execute(
            text("SELECT key, is_system FROM core.roles WHERE tenant_id = :t"), {"t": tid}
        ).all()
        grants: int = c.execute(
            text("SELECT count(*) FROM core.role_permissions WHERE tenant_id = :t"), {"t": tid}
        ).scalar_one()
    assert sorted(k for k, _ in roles) == sorted(
        [
            "accountant",
            "auditor_readonly",
            "class_teacher",
            "exam_coordinator",
            "office_admin",
            "office_staff",
            "owner",
            "principal",
            "teacher",
        ]
    )
    assert all(is_system for _, is_system in roles)
    with admin_engine.begin() as c:
        c.execute(
            text(
                "DELETE FROM core.role_permissions rp USING core.roles r "
                "WHERE rp.tenant_id = :t AND r.id = rp.role_id AND r.key = 'teacher' "
                "AND rp.permission_key = 'kb.ask'"
            ),
            {"t": tid},
        )
    with tenant_session(tid) as s:
        identity.clone_system_roles(s, tid)
    with admin_engine.connect() as c:
        again: int = c.execute(
            text("SELECT count(*) FROM core.role_permissions WHERE tenant_id = :t"), {"t": tid}
        ).scalar_one()
        events: list[str] = list(
            c.execute(
                text("SELECT action FROM audit.events WHERE tenant_id = :t ORDER BY seq"),
                {"t": tid},
            )
            .scalars()
            .all()
        )
    assert again == grants
    assert events.count("role.created") == 9
    assert events.count("role.permission_granted") == 1


def _wrapper() -> Any:
    from pydantic import SecretStr

    from app.core.config import Environment, KeyWrapperKind, Settings
    from app.core.crypto import LocalDevKeyWrapper

    return LocalDevKeyWrapper(
        Settings(
            env=Environment.CI,
            key_wrapper=KeyWrapperKind.LOCAL_DEV,
            local_dev_master_key=SecretStr("synthetic-ci-master-key-0123456789abcdef"),
        )
    )


@pytest.mark.db
def test_platform_permission_cannot_be_granted_even_by_hand(
    admin_engine: Engine, app_engine: Engine
) -> None:
    """DB trigger refuses platform.* grants to tenant roles (FR-IAM-010, SEC-026)."""
    tid = uuid.uuid4()
    with admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO core.tenants (id, code, name, status) VALUES (:t, :c, 'S', 'active')"
            ),
            {"t": tid, "c": f"t-{uuid.uuid4().hex[:12]}"},
        )
    role_id = uuid.uuid4()
    with tenant_session(tid) as s:
        s.execute(
            text(
                "INSERT INTO core.roles (id, tenant_id, key, name_en, name_te) "
                "VALUES (:r, :t, 'rogue', 'Rogue', 'Rogue')"
            ),
            {"r": role_id, "t": tid},
        )
    with pytest.raises(DBAPIError, match="platform permissions"):
        _grant(tid, role_id, "platform.tenants.read")


def _grant(tid: uuid.UUID, role_id: uuid.UUID, key: str) -> None:
    with tenant_session(tid) as s:
        s.execute(
            text(
                "INSERT INTO core.role_permissions (tenant_id, role_id, permission_key) "
                "VALUES (:t, :r, :k)"
            ),
            {"r": role_id, "t": tid, "k": key},
        )
