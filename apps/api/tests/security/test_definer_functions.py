"""Allowlisted SECURITY DEFINER functions of migration 0003 (ADR-0013, SEC-026, FR-IAM-013).

Each function must be owned by ``sos_definer``, pin ``search_path``, deny EXECUTE to PUBLIC and to
every role not listed, and return only the minimum it promises.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.core.db import context_free_session, platform_session, tenant_session
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError

pytestmark = pytest.mark.db

# function signature -> roles that may EXECUTE it
EXPECTED: dict[str, set[str]] = {
    "core.resolve_login(text)": {"sos_app"},
    "core.find_user_id_by_subject(text)": {"sos_app"},
    "core.create_user_for_invite(text,text,citext,text)": {"sos_app"},
    "core.list_tenant_ids(text[])": {"sos_app", "sos_platform"},
    "core.provision_tenant(uuid,text,text,text[],text,text)": {"sos_platform"},
    "core.set_tenant_status(uuid,text)": {"sos_platform"},
    "core.tenant_usage_summary(uuid)": {"sos_platform"},
}
ROLES = ("sos_app", "sos_platform", "sos_readonly", "sos_owner", "sos_migrator")
DEFINER_TABLES_NEEDED = {
    "core.tenants",
    "core.users",
    "core.memberships",
    "core.tenant_keys",
    "core.sections",
    "core.academic_years",
}


# --- helpers (admin engine = test setup only) ----------------------------------------------


def _code() -> str:
    return f"t-{uuid.uuid4().hex[:12]}"


def make_tenant(admin: Engine, status: str = "active") -> uuid.UUID:
    tid = uuid.uuid4()
    with admin.begin() as c:
        c.execute(
            text("INSERT INTO core.tenants (id, code, name, status) VALUES (:i, :c, 'School', :s)"),
            {"i": tid, "c": _code(), "s": status},
        )
    return tid


def make_user(admin: Engine, status: str = "active") -> tuple[uuid.UUID, str]:
    uid, subject = uuid.uuid4(), f"sub-{uuid.uuid4().hex}"
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO core.users (id, idp_subject, display_name, status) "
                "VALUES (:i, :s, 'Synthetic User', :st)"
            ),
            {"i": uid, "s": subject, "st": status},
        )
    return uid, subject


def make_membership(
    admin: Engine,
    tenant: uuid.UUID,
    user: uuid.UUID,
    status: str = "active",
    expires_at: datetime | None = None,
) -> uuid.UUID:
    mid = uuid.uuid4()
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO core.memberships (id, tenant_id, user_id, status, expires_at) "
                "VALUES (:i, :t, :u, :s, :e)"
            ),
            {"i": mid, "t": tenant, "u": user, "s": status, "e": expires_at},
        )
    return mid


def add_key(admin: Engine, tenant: uuid.UUID) -> None:
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO core.tenant_keys (tenant_id, key_version, wrapped_dek, wrapped_hmac, "
                "kms_key_arn) VALUES (:t, 1, '\\x00', '\\x00', 'local-dev:test')"
            ),
            {"t": tenant},
        )


def tenant_status(admin: Engine, tenant: uuid.UUID) -> str:
    with admin.connect() as c:
        return str(
            c.execute(text("SELECT status FROM core.tenants WHERE id = :t"), {"t": tenant}).scalar()
        )


@pytest.fixture
def engines(app_engine: Engine, platform_engine: Engine) -> None:
    """Bind core.db's app and platform engines to the test database."""


# --- catalog -------------------------------------------------------------------------------


def test_ADR_0013_definer_functions_owned_pinned_and_granted(admin_engine: Engine) -> None:
    with admin_engine.connect() as c:
        rows = c.execute(
            text(
                """
                SELECT p.oid, n.nspname || '.' || p.proname || '(' ||
                         pg_catalog.array_to_string(ARRAY(
                           SELECT pg_catalog.format_type(t, NULL)
                           FROM pg_catalog.unnest(p.proargtypes) AS t), ',') || ')' AS sig,
                       r.rolname AS owner, p.prosecdef, p.proconfig,
                       EXISTS (SELECT 1 FROM pg_catalog.aclexplode(p.proacl) a
                               WHERE a.grantee = 0) AS public_exec
                FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                JOIN pg_roles r ON r.oid = p.proowner
                WHERE n.nspname = 'core' AND p.prosecdef
                """
            )
        ).all()
        found = {r.sig.replace(" ", ""): r for r in rows}
        for sig, allowed in EXPECTED.items():
            assert sig in found, f"missing definer function {sig}"
            r = found[sig]
            assert r.owner == "sos_definer", sig
            assert r.proconfig == ["search_path=pg_catalog, pg_temp"], sig
            assert r.public_exec is False, f"PUBLIC can execute {sig}"
            for role in ROLES:
                can: bool = c.execute(
                    text("SELECT has_function_privilege(:r, :o, 'EXECUTE')"),
                    {"r": role, "o": r.oid},
                ).scalar_one()
                assert can is (role in allowed), f"{role} EXECUTE {sig} should be {role in allowed}"


def test_ADR_0013_definer_role_cannot_log_in_or_bypass_rls(admin_engine: Engine) -> None:
    with admin_engine.connect() as c:
        row = c.execute(
            text("SELECT rolcanlogin, rolbypassrls FROM pg_roles WHERE rolname = 'sos_definer'")
        ).one()
    assert (row.rolcanlogin, row.rolbypassrls) == (False, False)


def test_ADR_0013_definer_access_present_on_tables_functions_need(admin_engine: Engine) -> None:
    with admin_engine.connect() as c:
        tables: set[str] = set(
            c.execute(
                text(
                    "SELECT schemaname || '.' || tablename FROM pg_policies "
                    "WHERE policyname = 'definer_access' AND schemaname = 'core'"
                )
            ).scalars()
        )
    assert tables >= DEFINER_TABLES_NEEDED


def test_ADR_0013_create_grant_on_core_was_revoked_from_definer(admin_engine: Engine) -> None:
    with admin_engine.connect() as c:
        assert (
            c.execute(text("SELECT has_schema_privilege('sos_definer', 'core', 'CREATE')")).scalar()
            is False
        )


# --- resolve_login -------------------------------------------------------------------------


def test_FR_IAM_013_resolve_login_returns_only_active_unexpired_memberships(
    admin_engine: Engine, engines: None
) -> None:
    uid, subject = make_user(admin_engine)
    t_active, t_expired, t_suspended, t_future, t_invited = (
        make_tenant(admin_engine) for _ in range(5)
    )
    m_active = make_membership(admin_engine, t_active, uid)
    make_membership(admin_engine, t_suspended, uid, status="suspended")
    make_membership(admin_engine, t_invited, uid, status="invited")
    m_future = make_membership(
        admin_engine, t_future, uid, expires_at=datetime.now(UTC) + timedelta(days=14)
    )
    m_expired = make_membership(
        admin_engine, t_expired, uid, expires_at=datetime.now(UTC) + timedelta(days=1)
    )
    with admin_engine.begin() as c:  # move expiry into the past, bypassing the creation CHECK
        c.execute(
            text(
                "UPDATE core.memberships SET created_at = now() - interval '3 days', "
                "expires_at = now() - interval '1 day' WHERE id = :m"
            ),
            {"m": m_expired},
        )
    with context_free_session() as s:
        rows = s.execute(text("SELECT * FROM core.resolve_login(:s)"), {"s": subject}).all()
    got = {(r.user_id, r.tenant_id, r.membership_id, r.tenant_status) for r in rows}
    assert got == {
        (uid, t_active, m_active, "active"),
        (uid, t_future, m_future, "active"),
    }
    assert list(rows[0]._fields) == ["user_id", "tenant_id", "membership_id", "tenant_status"]


def test_FR_IAM_013_resolve_login_nothing_for_disabled_or_unknown_user(
    admin_engine: Engine, engines: None
) -> None:
    uid, subject = make_user(admin_engine, status="disabled")
    make_membership(admin_engine, make_tenant(admin_engine), uid)
    with context_free_session() as s:
        assert s.execute(text("SELECT * FROM core.resolve_login(:s)"), {"s": subject}).all() == []
        assert (
            s.execute(text("SELECT * FROM core.resolve_login(:s)"), {"s": "no-such-sub"}).all()
            == []
        )


def test_FR_IAM_013_resolve_login_reports_suspended_tenant_status(
    admin_engine: Engine, engines: None
) -> None:
    uid, subject = make_user(admin_engine)
    tid = make_tenant(admin_engine, status="suspended")
    make_membership(admin_engine, tid, uid)
    with context_free_session() as s:
        row = s.execute(text("SELECT * FROM core.resolve_login(:s)"), {"s": subject}).one()
    assert row.tenant_status == "suspended"


@pytest.mark.parametrize("engine_name", ["platform_engine", "readonly_engine"])
def test_ADR_0013_resolve_login_denied_to_other_roles(
    engine_name: str, request: pytest.FixtureRequest
) -> None:
    engine: Engine = request.getfixturevalue(engine_name)
    with pytest.raises(ProgrammingError, match="permission denied"), engine.begin() as c:
        c.execute(text("SELECT * FROM core.resolve_login('x')"))


# --- find_user_id_by_subject ---------------------------------------------------------------


def test_FR_IAM_013_find_user_id_by_subject(admin_engine: Engine, engines: None) -> None:
    uid, subject = make_user(admin_engine)
    with context_free_session() as s:
        q = text("SELECT core.find_user_id_by_subject(:s)")
        assert s.execute(q, {"s": subject}).scalar_one() == uid
        assert s.execute(q, {"s": "nobody"}).scalar_one() is None


# --- create_user_for_invite ----------------------------------------------------------------

INVITE = text("SELECT core.create_user_for_invite(:s, :n, CAST(:e AS public.citext), :l)")


def _invite_args(subject: str | None = None) -> dict[str, str]:
    return {
        "s": subject or f"sub-{uuid.uuid4().hex}",
        "n": "Invited Teacher",
        "e": "Teacher@Example.test",
        "l": "te",
    }


def test_FR_IAM_010_invite_requires_tenant_and_inviter_context(engines: None) -> None:
    with (
        pytest.raises(ProgrammingError, match="tenant and inviter context"),
        context_free_session() as s,
    ):
        s.execute(INVITE, _invite_args())


def test_FR_IAM_010_invite_refused_without_user_context(
    admin_engine: Engine, engines: None
) -> None:
    tid = make_tenant(admin_engine)
    with pytest.raises(ProgrammingError, match="inviter context"), tenant_session(tid) as s:
        s.execute(INVITE, _invite_args())


@pytest.mark.parametrize("case", ["other_tenant", "suspended", "expired", "disabled_user"])
def test_FR_IAM_010_invite_refused_without_active_inviter_membership(
    case: str, admin_engine: Engine, engines: None
) -> None:
    tid, other = make_tenant(admin_engine), make_tenant(admin_engine)
    inviter, _ = make_user(admin_engine, status="disabled" if case == "disabled_user" else "active")
    if case == "other_tenant":
        make_membership(admin_engine, other, inviter)
    elif case == "suspended":
        make_membership(admin_engine, tid, inviter, status="suspended")
    elif case == "expired":
        mid = make_membership(
            admin_engine, tid, inviter, expires_at=datetime.now(UTC) + timedelta(1)
        )
        with admin_engine.begin() as c:
            c.execute(
                text(
                    "UPDATE core.memberships SET created_at = now() - interval '3 days', "
                    "expires_at = now() - interval '1 day' WHERE id = :m"
                ),
                {"m": mid},
            )
    else:
        make_membership(admin_engine, tid, inviter)
    with (
        pytest.raises(ProgrammingError, match="no active membership"),
        tenant_session(tid, inviter) as s,
    ):
        s.execute(INVITE, _invite_args())


def test_FR_IAM_010_invite_refused_in_suspended_tenant(admin_engine: Engine, engines: None) -> None:
    tid = make_tenant(admin_engine, status="suspended")
    inviter, _ = make_user(admin_engine)
    make_membership(admin_engine, tid, inviter)
    with (
        pytest.raises(ProgrammingError, match="no active membership"),
        tenant_session(tid, inviter) as s,
    ):
        s.execute(INVITE, _invite_args())


def test_FR_IAM_010_invite_creates_user_and_returns_existing_id_without_overwrite(
    admin_engine: Engine, engines: None
) -> None:
    tid = make_tenant(admin_engine)
    inviter, _ = make_user(admin_engine)
    make_membership(admin_engine, tid, inviter)
    args = _invite_args()
    with tenant_session(tid, inviter) as s:
        new_id: uuid.UUID = s.execute(INVITE, args).scalar_one()
    assert isinstance(new_id, uuid.UUID)
    assert new_id.version == 7
    with admin_engine.connect() as c:
        row = c.execute(text("SELECT * FROM core.users WHERE id = :i"), {"i": new_id}).one()
    assert (row.idp_subject, row.display_name, row.email, row.preferred_language, row.status) == (
        args["s"],
        "Invited Teacher",
        "Teacher@Example.test",
        "te",
        "active",
    )
    # Same subject again (e.g. invited by a second school): same id, nothing overwritten.
    other = make_tenant(admin_engine)
    inviter2, _ = make_user(admin_engine)
    make_membership(admin_engine, other, inviter2)
    with tenant_session(other, inviter2) as s:
        again: uuid.UUID = s.execute(INVITE, {**args, "n": "Changed Name", "l": "en"}).scalar_one()
    assert again == new_id
    with admin_engine.connect() as c:
        name: str = c.execute(
            text("SELECT display_name FROM core.users WHERE id = :i"), {"i": new_id}
        ).scalar_one()
    assert name == "Invited Teacher"


def test_FR_IAM_010_invite_returns_only_an_id(admin_engine: Engine) -> None:
    with admin_engine.connect() as c:
        rettype = c.execute(
            text(
                "SELECT pg_catalog.format_type(prorettype, NULL), proretset FROM pg_proc "
                "WHERE oid = 'core.create_user_for_invite(text,text,public.citext,text)'"
                "::regprocedure"
            )
        ).one()
    assert tuple(rettype) == ("uuid", False)


def test_FR_IAM_010_new_user_invisible_to_inviting_tenant_until_membership_exists(
    admin_engine: Engine, engines: None
) -> None:
    tid = make_tenant(admin_engine)
    inviter, _ = make_user(admin_engine)
    make_membership(admin_engine, tid, inviter)
    with tenant_session(tid, inviter) as s:
        new_id: uuid.UUID = s.execute(INVITE, _invite_args()).scalar_one()
        assert (
            s.execute(text("SELECT count(*) FROM core.users WHERE id = :i"), {"i": new_id}).scalar()
            == 0
        )
        s.execute(
            text(
                "INSERT INTO core.memberships (id, tenant_id, user_id, status) "
                "VALUES (:m, :t, :u, 'invited')"
            ),
            {"m": uuid.uuid4(), "t": tid, "u": new_id},
        )
        assert (
            s.execute(text("SELECT count(*) FROM core.users WHERE id = :i"), {"i": new_id}).scalar()
            == 1
        )


# --- list_tenant_ids -----------------------------------------------------------------------


def test_ADR_0013_list_tenant_ids_filters_by_status(admin_engine: Engine, engines: None) -> None:
    active, suspended = make_tenant(admin_engine), make_tenant(admin_engine, "suspended")
    q = text("SELECT * FROM core.list_tenant_ids(:s)")
    with context_free_session() as s:
        # Interface contract with the audit module: the column is named tenant_id.
        assert list(s.execute(q, {"s": ["active"]}).keys()) == ["tenant_id"]
        ids: set[uuid.UUID] = set(s.execute(q, {"s": ["active"]}).scalars())
        everything: set[uuid.UUID] = set(s.execute(q, {"s": None}).scalars())
    assert active in ids
    assert suspended not in ids
    assert {active, suspended} <= everything
    with platform_session() as ps:
        assert suspended in set(ps.execute(q, {"s": ["suspended"]}).scalars())


def test_ADR_0013_list_tenant_ids_denied_to_readonly(readonly_engine: Engine) -> None:
    with pytest.raises(ProgrammingError, match="permission denied"), readonly_engine.begin() as c:
        c.execute(text("SELECT * FROM core.list_tenant_ids(NULL)"))


# --- provision_tenant / set_tenant_status --------------------------------------------------

PROVISION = text("SELECT core.provision_tenant(:i, :c, :n, CAST(:b AS text[]), :p, :d)")


def _provision(tid: uuid.UUID, code: str | None = None) -> uuid.UUID:
    with platform_session() as ps:
        return uuid.UUID(
            str(
                ps.execute(
                    PROVISION,
                    {
                        "i": tid,
                        "c": code or _code(),
                        "n": "Synthetic School",
                        "b": ["CISCE"],
                        "p": "shared",
                        "d": "shared",
                    },
                ).scalar_one()
            )
        )


def test_FR_TEN_003_provision_tenant_creates_row_in_provisioning(
    admin_engine: Engine, engines: None
) -> None:
    tid = uuid.uuid4()
    assert _provision(tid) == tid
    with admin_engine.connect() as c:
        row = c.execute(text("SELECT * FROM core.tenants WHERE id = :t"), {"t": tid}).one()
    assert (row.status, row.boards, row.plan_tier, row.deployment_mode) == (
        "provisioning",
        ["CISCE"],
        "shared",
        "shared",
    )


def test_FR_TEN_003_provision_tenant_rejects_duplicate_code(engines: None) -> None:
    code = _code()
    _provision(uuid.uuid4(), code)
    with pytest.raises(IntegrityError, match="tenants_code_key"):
        _provision(uuid.uuid4(), code)


def test_ADR_0013_provision_tenant_denied_to_app(engines: None) -> None:
    with pytest.raises(ProgrammingError, match="permission denied"), context_free_session() as s:
        s.execute(
            PROVISION,
            {"i": uuid.uuid4(), "c": _code(), "n": "x", "b": [], "p": "shared", "d": "shared"},
        )


def _set_status(tid: uuid.UUID, status: str) -> str:
    with platform_session() as ps:
        return str(
            ps.execute(
                text("SELECT core.set_tenant_status(:t, :s)"), {"t": tid, "s": status}
            ).scalar_one()
        )


def test_FR_TEN_003_set_tenant_status_legal_lifecycle(admin_engine: Engine, engines: None) -> None:
    tid = _provision(uuid.uuid4())
    add_key(admin_engine, tid)
    steps = ["active", "suspended", "active", "offboarding", "deleted"]
    previous = "provisioning"
    for status in steps:
        assert _set_status(tid, status) == previous
        assert tenant_status(admin_engine, tid) == status
        previous = status
    with admin_engine.connect() as c:
        assert c.execute(
            text("SELECT version FROM core.tenants WHERE id = :t"), {"t": tid}
        ).scalar() == 1 + len(steps)


@pytest.mark.parametrize(
    ("start", "target"),
    [
        ("provisioning", "suspended"),
        ("provisioning", "deleted"),
        ("active", "deleted"),
        ("active", "provisioning"),
        ("suspended", "deleted"),
        ("offboarding", "active"),
        ("deleted", "active"),
        ("active", "bogus"),
    ],
)
def test_FR_TEN_003_set_tenant_status_rejects_illegal_transitions(
    start: str, target: str, admin_engine: Engine, engines: None
) -> None:
    tid = make_tenant(admin_engine, status=start)
    with pytest.raises(DBAPIError, match="illegal tenant status transition"):
        _set_status(tid, target)
    assert tenant_status(admin_engine, tid) == start


def test_FR_TEN_003_cannot_activate_without_data_encryption_key(
    admin_engine: Engine, engines: None
) -> None:
    tid = _provision(uuid.uuid4())
    with pytest.raises(DBAPIError, match="no data encryption key"):
        _set_status(tid, "active")


def test_FR_TEN_003_set_tenant_status_unknown_tenant(engines: None) -> None:
    with pytest.raises(DBAPIError, match="tenant not found"):
        _set_status(uuid.uuid4(), "active")


def test_ADR_0013_set_tenant_status_denied_to_app(admin_engine: Engine, engines: None) -> None:
    tid = make_tenant(admin_engine, status="active")
    with pytest.raises(ProgrammingError, match="permission denied"), tenant_session(tid) as s:
        s.execute(text("SELECT core.set_tenant_status(:t, 'suspended')"), {"t": tid})


# --- tenant_usage_summary ------------------------------------------------------------------


def test_FR_PLT_020_usage_summary_counts_only(admin_engine: Engine, engines: None) -> None:
    tid = make_tenant(admin_engine)
    u1, _ = make_user(admin_engine)
    u2, _ = make_user(admin_engine)
    u3, _ = make_user(admin_engine)
    make_membership(admin_engine, tid, u1)
    make_membership(admin_engine, tid, u2, status="invited")
    make_membership(admin_engine, tid, u3, status="removed")
    with admin_engine.begin() as c:
        year, klass = uuid.uuid4(), uuid.uuid4()
        c.execute(
            text(
                "INSERT INTO core.academic_years (id, tenant_id, label, starts_on, ends_on) "
                "VALUES (:y, :t, '2026-27', '2026-06-01', '2027-04-30')"
            ),
            {"y": year, "t": tid},
        )
        c.execute(
            text(
                "INSERT INTO core.classes "
                "(id, tenant_id, code, display_en, display_te, sort_order) "
                "VALUES (:c, :t, 'IX', 'Class IX', '9వ తరగతి', 12)"
            ),
            {"c": klass, "t": tid},
        )
        for name in ("A", "B"):
            c.execute(
                text(
                    "INSERT INTO core.sections (id, tenant_id, class_id, academic_year_id, name) "
                    "VALUES (:i, :t, :c, :y, :n)"
                ),
                {"i": uuid.uuid4(), "t": tid, "c": klass, "y": year, "n": name},
            )
    with platform_session() as ps:
        row = ps.execute(text("SELECT * FROM core.tenant_usage_summary(:t)"), {"t": tid}).one()
    assert row._asdict() == {
        "active_memberships": 1,
        "users": 2,
        "sections": 2,
        "academic_years": 1,
    }


def test_ADR_0013_usage_summary_denied_to_app(engines: None) -> None:
    with pytest.raises(ProgrammingError, match="permission denied"), context_free_session() as s:
        s.execute(text("SELECT * FROM core.tenant_usage_summary(:t)"), {"t": uuid.uuid4()})


def test_ADR_0013_platform_role_still_cannot_read_core_tables(engines: None) -> None:
    with pytest.raises(ProgrammingError, match="permission denied"), platform_session() as ps:
        ps.execute(text("SELECT count(*) FROM core.users"))
