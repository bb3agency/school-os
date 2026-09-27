"""Identity repository (FR-IAM-010..013, ADR-0013)."""

from __future__ import annotations

import datetime as dt
import unicodedata
import uuid
from collections.abc import Callable

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.db import context_free_session, tenant_session
from app.core.errors import Forbidden, PreconditionFailed
from app.identity import repository as repo

pytestmark = pytest.mark.db
STAFF = get_settings().oidc_issuer


@pytest.fixture
def school(admin_engine: Engine, app_engine: Engine) -> Callable[..., uuid.UUID]:
    def _make(status: str = "active") -> uuid.UUID:
        tid = uuid.uuid4()
        with admin_engine.begin() as c:
            c.execute(
                text("INSERT INTO core.tenants (id, code, name, status) VALUES (:i, :c, 'S', :s)"),
                {"i": tid, "c": f"t-{uuid.uuid4().hex[:12]}", "s": status},
            )
        return tid

    return _make


@pytest.fixture
def admin_member(admin_engine: Engine) -> Callable[[uuid.UUID], tuple[uuid.UUID, str]]:
    """An active staff member created out of band (the first inviter)."""

    def _make(tenant: uuid.UUID) -> tuple[uuid.UUID, str]:
        uid, subject = uuid.uuid4(), f"sub-{uuid.uuid4().hex}"
        with admin_engine.begin() as c:
            c.execute(
                text(
                    "INSERT INTO core.users (id, idp_subject, display_name) "
                    "VALUES (:u, :s, 'Admin')"
                ),
                {"u": uid, "s": subject},
            )
            c.execute(
                text(
                    "INSERT INTO core.memberships (id, tenant_id, user_id, status) "
                    "VALUES (gen_random_uuid(), :t, :u, 'active')"
                ),
                {"t": tenant, "u": uid},
            )
        return uid, subject

    return _make


def _invite(tenant: uuid.UUID, inviter: uuid.UUID, subject: str | None = None) -> uuid.UUID:
    with tenant_session(tenant, inviter) as s:
        return repo.create_user_for_invite(
            s,
            subject=subject or f"sub-{uuid.uuid4().hex}",
            issuer=STAFF,
            display_name=unicodedata.normalize("NFD", " Lakshmī Devi "),
            email="Lakshmi@Example.test",
            language="te",
        )


def test_FR_IAM_010_invite_flow_creates_user_and_membership(
    school: Callable[..., uuid.UUID], admin_member: Callable[[uuid.UUID], tuple[uuid.UUID, str]]
) -> None:
    tid = school()
    inviter, _ = admin_member(tid)
    subject = f"sub-{uuid.uuid4().hex}"
    user_id = _invite(tid, inviter, subject)
    with tenant_session(tid, inviter) as s:
        assert repo.get_user(s, user_id) is None  # invisible until a membership exists
        m = repo.create_membership(s, user_id=user_id, created_by=inviter)
        assert (m.status, m.tenant_id, m.created_by, m.version) == ("invited", tid, inviter, 1)
        user = repo.get_user(s, user_id)
        assert user is not None
        assert user.display_name == unicodedata.normalize("NFC", "Lakshmī Devi")
        assert user.preferred_language == "te"
    with context_free_session() as s:
        assert repo.find_user_id_by_subject(s, subject, issuer=STAFF) == user_id
        assert repo.resolve_login(s, subject, issuer=STAFF) == []  # still invited


def test_FR_IAM_010_invite_without_inviter_membership_is_forbidden(
    school: Callable[..., uuid.UUID], admin_member: Callable[[uuid.UUID], tuple[uuid.UUID, str]]
) -> None:
    a, b = school(), school()
    inviter_in_b, _ = admin_member(b)
    with pytest.raises(Forbidden):
        _invite(a, inviter_in_b)


def test_FR_IAM_013_resolve_login_after_activation(
    school: Callable[..., uuid.UUID], admin_member: Callable[[uuid.UUID], tuple[uuid.UUID, str]]
) -> None:
    a, b = school(), school()
    inviter_a, _ = admin_member(a)
    inviter_b, _ = admin_member(b)
    subject = f"sub-{uuid.uuid4().hex}"
    user_id = _invite(a, inviter_a, subject)
    assert _invite(b, inviter_b, subject) == user_id  # same person at two schools
    memberships: dict[uuid.UUID, uuid.UUID] = {}
    for tid, inviter in ((a, inviter_a), (b, inviter_b)):
        with tenant_session(tid, inviter) as s:
            m = repo.create_membership(s, user_id=user_id, created_by=inviter)
            repo.set_membership_status(s, m.id, status="active", expected_version=m.version)
            memberships[tid] = m.id
    with context_free_session() as s:
        logins = repo.resolve_login(s, subject, issuer=STAFF)
    assert {(x.tenant_id, x.membership_id, x.tenant_status) for x in logins} == {
        (a, memberships[a], "active"),
        (b, memberships[b], "active"),
    }
    assert {x.user_id for x in logins} == {user_id}
    with tenant_session(a, user_id) as s:
        repo.record_login(s, user_id)
        user = repo.get_user(s, user_id)
        assert user is not None
        assert user.last_login_at is not None
        # Tenant A sees only its own membership for this person.
        assert [m.tenant_id for m in repo.list_memberships_for_user(s, user_id)] == [a]


def test_FR_IAM_010_membership_status_optimistic_lock_and_expiry(
    school: Callable[..., uuid.UUID], admin_member: Callable[[uuid.UUID], tuple[uuid.UUID, str]]
) -> None:
    tid = school()
    inviter, _ = admin_member(tid)
    user_id = _invite(tid, inviter)
    expires = dt.datetime.now(dt.UTC) + dt.timedelta(days=14)
    with tenant_session(tid, inviter) as s:
        m = repo.create_membership(s, user_id=user_id, status="active", expires_at=expires)
        assert m.expires_at == expires
        updated = repo.set_membership_status(s, m.id, status="suspended", expected_version=1)
        assert (updated.status, updated.version) == ("suspended", 2)
    with pytest.raises(PreconditionFailed), tenant_session(tid, inviter) as s:
        repo.set_membership_status(s, m.id, status="active", expected_version=1)
    with pytest.raises(IntegrityError), tenant_session(tid, inviter) as s:
        repo.create_membership(s, user_id=user_id)  # one membership per user per school


def test_FR_IAM_011_roles_permissions_and_scopes(
    school: Callable[..., uuid.UUID],
    admin_member: Callable[[uuid.UUID], tuple[uuid.UUID, str]],
    admin_engine: Engine,
) -> None:
    with admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO core.permissions (key, description) VALUES "
                "('student.read_basic', 'Read'), ('document.read', 'Read docs') "
                "ON CONFLICT DO NOTHING"
            )
        )
    tid = school()
    inviter, _ = admin_member(tid)
    user_id = _invite(tid, inviter)
    with tenant_session(tid, inviter) as s:
        m = repo.create_membership(s, user_id=user_id, status="active")
        role = repo.create_role(
            s, key="class_teacher", name_en="Class teacher", name_te="తరగతి ఉపాధ్యాయుడు", is_system=True
        )
        repo.grant_role_permission(s, role.id, "student.read_basic")
        repo.grant_role_permission(s, role.id, "document.read")
        repo.grant_role_permission(s, role.id, "document.read")  # idempotent
        repo.add_membership_role(s, m.id, role.id, granted_by=inviter)
        assert repo.get_role_by_key(s, "class_teacher") == role
        assert [r.key for r in repo.list_roles_for_membership(s, m.id)] == ["class_teacher"]
        assert repo.permission_keys_for_membership(s, m.id) == {
            "student.read_basic",
            "document.read",
        }
        scope = repo.add_membership_scope(s, m.id, "school")
        assert [x.scope_type for x in repo.list_membership_scopes(s, m.id)] == ["school"]
        assert repo.remove_membership_scope(s, scope.id) is True
        assert repo.list_membership_scopes(s, m.id) == []
        assert repo.remove_membership_role(s, m.id, role.id) is True
        assert repo.permission_keys_for_membership(s, m.id) == set()
        assert {p.key for p in repo.list_permissions(s)} >= {"student.read_basic", "document.read"}


def test_FR_IAM_012_permissions_of_suspended_or_expired_membership_are_empty(
    school: Callable[..., uuid.UUID],
    admin_member: Callable[[uuid.UUID], tuple[uuid.UUID, str]],
    admin_engine: Engine,
) -> None:
    with admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO core.permissions (key, description) "
                "VALUES ('student.read_basic', 'R') "
                "ON CONFLICT DO NOTHING"
            )
        )
    tid = school()
    inviter, _ = admin_member(tid)
    user_id = _invite(tid, inviter)
    with tenant_session(tid, inviter) as s:
        m = repo.create_membership(s, user_id=user_id, status="suspended")
        role = repo.create_role(s, key="teacher", name_en="Teacher", name_te="ఉపాధ్యాయుడు")
        repo.grant_role_permission(s, role.id, "student.read_basic")
        repo.add_membership_role(s, m.id, role.id)
        assert repo.permission_keys_for_membership(s, m.id) == set()


def test_FR_TEN_002_other_tenants_user_and_membership_not_found(
    school: Callable[..., uuid.UUID], admin_member: Callable[[uuid.UUID], tuple[uuid.UUID, str]]
) -> None:
    a, b = school(), school()
    ua, _ = admin_member(a)
    ub, _ = admin_member(b)
    with tenant_session(b, ub) as s:
        mb = repo.list_memberships_for_user(s, ub)[0]
    with tenant_session(a, ua) as s:
        assert repo.get_user(s, ub) is None
        assert repo.get_membership(s, mb.id) is None
        assert repo.list_memberships_for_user(s, ub) == []
