"""Database-level guarantees of migration 0003 (FR-TEN-001/002/010, FR-IAM-011/012, SEC-001).

Explicit tenant A / tenant B visibility per table; the generic suite only checks "empty without
context".
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from app.core.db import tenant_session
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

pytestmark = pytest.mark.db

MakeTenant = Callable[..., uuid.UUID]
MakeMember = Callable[..., tuple[uuid.UUID, uuid.UUID]]


def _seed_structure(tenant: uuid.UUID) -> dict[str, uuid.UUID]:
    """As sos_app inside the tenant: one year, class, section and role."""
    ids = {k: uuid.uuid4() for k in ("year", "class", "section", "role")}
    with tenant_session(tenant) as s:
        s.execute(
            text(
                "INSERT INTO core.academic_years (id, tenant_id, label, starts_on, ends_on) "
                "VALUES (:y, :t, '2026-27', '2026-06-01', '2027-04-30')"
            ),
            {"y": ids["year"], "t": tenant},
        )
        s.execute(
            text(
                "INSERT INTO core.classes "
                "(id, tenant_id, code, display_en, display_te, sort_order) "
                "VALUES (:c, :t, 'IX', 'Class IX', '9వ తరగతి', 120)"
            ),
            {"c": ids["class"], "t": tenant},
        )
        s.execute(
            text(
                "INSERT INTO core.sections (id, tenant_id, class_id, academic_year_id, name) "
                "VALUES (:s, :t, :c, :y, 'A')"
            ),
            {"s": ids["section"], "t": tenant, "c": ids["class"], "y": ids["year"]},
        )
        s.execute(
            text(
                "INSERT INTO core.roles (id, tenant_id, key, name_en, name_te) "
                "VALUES (:r, :t, 'class_teacher', 'Class teacher', 'తరగతి ఉపాధ్యాయుడు')"
            ),
            {"r": ids["role"], "t": tenant},
        )
        s.execute(
            text(
                "INSERT INTO core.tenant_keys (tenant_id, key_version, wrapped_dek, wrapped_hmac, "
                "kms_key_arn) VALUES (:t, 1, '\\x01', '\\x02', 'local-dev:test')"
            ),
            {"t": tenant},
        )
    return ids


# --- tenant isolation ---------------------------------------------------------------------

ISOLATED_TABLES = [
    "core.academic_years",
    "core.classes",
    "core.sections",
    "core.roles",
    "core.tenant_keys",
    "core.memberships",
]


def test_FR_TEN_002_tenancy_tables_visible_only_to_own_tenant(
    make_tenant: MakeTenant, make_member: MakeMember
) -> None:
    a, b = make_tenant(), make_tenant()
    for t in (a, b):
        _seed_structure(t)
        make_member(t)
    for tenant in (a, b):
        with tenant_session(tenant) as s:
            for table in ISOLATED_TABLES:
                seen: set[uuid.UUID] = set(
                    s.execute(text(f"SELECT DISTINCT tenant_id FROM {table}")).scalars()
                )
                assert seen == {tenant}, table
            assert set(s.execute(text("SELECT id FROM core.tenants")).scalars()) == {tenant}


def test_FR_TEN_002_other_tenants_rows_cannot_be_fetched_or_changed_by_id(
    make_tenant: MakeTenant,
) -> None:
    a, b = make_tenant(), make_tenant()
    b_ids = _seed_structure(b)
    with tenant_session(a) as s:
        assert (
            s.execute(
                text("SELECT count(*) FROM core.sections WHERE id = :i"), {"i": b_ids["section"]}
            ).scalar()
            == 0
        )
        updated = s.execute(
            text("UPDATE core.classes SET display_en = 'hijack' WHERE id = :i"),
            {"i": b_ids["class"]},
        )
        assert updated.rowcount == 0  # type: ignore[attr-defined]
        deleted = s.execute(text("DELETE FROM core.roles WHERE id = :i"), {"i": b_ids["role"]})
        assert deleted.rowcount == 0  # type: ignore[attr-defined]
        assert (
            s.execute(text("SELECT count(*) FROM core.tenants WHERE id = :i"), {"i": b}).scalar()
            == 0
        )


def test_FR_TEN_002_insert_into_other_tenant_rejected(make_tenant: MakeTenant) -> None:
    a, b = make_tenant(), make_tenant()
    with pytest.raises(ProgrammingError, match="row-level security"), tenant_session(a) as s:
        s.execute(
            text(
                "INSERT INTO core.classes "
                "(id, tenant_id, code, display_en, display_te, sort_order) "
                "VALUES (gen_random_uuid(), :b, 'X', 'Class X', '10వ తరగతి', 130)"
            ),
            {"b": b},
        )


def test_FR_IAM_013_users_visible_only_through_membership_in_current_tenant(
    make_tenant: MakeTenant, make_member: MakeMember
) -> None:
    a, b = make_tenant(), make_tenant()
    ua, _ = make_member(a)
    ub, _ = make_member(b)
    with tenant_session(a) as s:
        visible: set[uuid.UUID] = set(
            s.execute(
                text("SELECT id FROM core.users WHERE id IN (:x, :y)"), {"x": ua, "y": ub}
            ).scalars()
        )
        assert visible == {ua}
        changed = s.execute(
            text("UPDATE core.users SET display_name = 'hijack' WHERE id = :u"), {"u": ub}
        )
        assert changed.rowcount == 0  # type: ignore[attr-defined]


# --- grants ------------------------------------------------------------------------------

DENIED_FOR_APP = [
    "INSERT INTO core.tenants (id, code, name) VALUES (core.current_tenant(), 'x-new', 'x')",
    "DELETE FROM core.tenants",
    "UPDATE core.tenants SET status = 'active'",
    "UPDATE core.tenants SET plan_tier = 'dedicated'",
    "INSERT INTO core.users (id, idp_subject, display_name) VALUES (gen_random_uuid(), 's', 'n')",
    "DELETE FROM core.users",
    "UPDATE core.users SET idp_subject = 'other'",
    "UPDATE core.users SET status = 'disabled'",
    "INSERT INTO core.permissions (key, description) VALUES ('x.y', 'd')",
    "UPDATE core.permissions SET step_up = true",
    "DELETE FROM core.permissions",
    "DELETE FROM core.tenant_keys",
    "UPDATE core.tenant_keys SET wrapped_dek = '\\x00'",
]


@pytest.mark.parametrize("sql", DENIED_FOR_APP)
def test_ADR_0013_app_role_privileges_are_narrowed(sql: str, make_tenant: MakeTenant) -> None:
    with (
        pytest.raises(ProgrammingError, match="permission denied"),
        tenant_session(make_tenant()) as s,
    ):
        s.execute(text(sql))


def test_ADR_0013_app_may_edit_own_tenant_name_and_settings(
    make_tenant: MakeTenant, admin_engine: Engine
) -> None:
    tid = make_tenant()
    with tenant_session(tid) as s:
        s.execute(
            text(
                "UPDATE core.tenants SET name = 'Renamed School', "
                'settings = \'{"languages": ["en", "te"]}\', version = version + 1'
            )
        )
    with admin_engine.connect() as c:
        row = c.execute(
            text(
                "SELECT name, version, updated_at > created_at AS touched "
                "FROM core.tenants WHERE id = :t"
            ),
            {"t": tid},
        ).one()
    assert (row.name, row.version, row.touched) == ("Renamed School", 2, True)


def test_ADR_0013_readonly_role_cannot_read_wrapped_keys(readonly_engine: Engine) -> None:
    with pytest.raises(ProgrammingError, match="permission denied"), readonly_engine.begin() as c:
        c.execute(text("SELECT * FROM core.tenant_keys"))


# --- constraints -------------------------------------------------------------------------


def test_FR_TEN_010_only_one_current_year_per_tenant(make_tenant: MakeTenant) -> None:
    tid = make_tenant()
    with tenant_session(tid) as s:
        s.execute(
            text(
                "INSERT INTO core.academic_years "
                "(id, tenant_id, label, starts_on, ends_on, is_current) "
                "VALUES (gen_random_uuid(), :t, '2025-26', '2025-06-01', '2026-04-30', true)"
            ),
            {"t": tid},
        )
    with pytest.raises(IntegrityError, match="one_current_year"), tenant_session(tid) as s:
        s.execute(
            text(
                "INSERT INTO core.academic_years "
                "(id, tenant_id, label, starts_on, ends_on, is_current) "
                "VALUES (gen_random_uuid(), :t, '2026-27', '2026-06-01', '2027-04-30', true)"
            ),
            {"t": tid},
        )
    # Another tenant may have its own current year.
    other = make_tenant()
    with tenant_session(other) as s:
        s.execute(
            text(
                "INSERT INTO core.academic_years "
                "(id, tenant_id, label, starts_on, ends_on, is_current) "
                "VALUES (gen_random_uuid(), :t, '2026-27', '2026-06-01', '2027-04-30', true)"
            ),
            {"t": other},
        )


@pytest.mark.parametrize(
    ("sql", "constraint"),
    [
        (
            "INSERT INTO core.academic_years (id, tenant_id, label, starts_on, ends_on) "
            "VALUES (gen_random_uuid(), :t, 'bad', '2027-06-01', '2026-06-01')",
            "academic_years_dates_ordered",
        ),
        (
            "INSERT INTO core.roles (id, tenant_id, key, name_en, name_te) "
            "VALUES (gen_random_uuid(), :t, 'Bad-Key', 'x', 'x')",
            "roles_key_format",
        ),
        (
            "INSERT INTO core.roles (id, tenant_id, key, name_en, name_te) "
            "VALUES (gen_random_uuid(), :t, 'x', 'x', 'x')",
            "roles_key_format",
        ),
        (
            "INSERT INTO core.classes (id, tenant_id, code, display_en, display_te, sort_order) "
            "VALUES (gen_random_uuid(), :t, 'ix', 'x', 'x', 1)",
            "classes_code_format",
        ),
        (
            "INSERT INTO core.memberships (id, tenant_id, user_id, status) "
            "SELECT gen_random_uuid(), :t, gen_random_uuid(), 'pending'",
            "memberships_status_check",
        ),
    ],
)
def test_FR_TEN_010_check_constraints(sql: str, constraint: str, make_tenant: MakeTenant) -> None:
    with pytest.raises(IntegrityError, match=constraint), tenant_session(make_tenant()) as s:
        s.execute(text(sql), {"t": s.execute(text("SELECT core.current_tenant()")).scalar()})


def test_FR_IAM_011_platform_permission_cannot_be_granted_to_tenant_role(
    make_tenant: MakeTenant, admin_engine: Engine
) -> None:
    with admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO core.permissions (key, description, sensitivity, is_platform) VALUES "
                "('platform.tenants.read', 'Read tenants', 'normal', true), "
                "('student.read_basic', 'Read students', 'normal', false) ON CONFLICT DO NOTHING"
            )
        )
    tid = make_tenant()
    ids = _seed_structure(tid)
    with tenant_session(tid) as s:
        s.execute(
            text(
                "INSERT INTO core.role_permissions (tenant_id, role_id, permission_key) "
                "VALUES (:t, :r, 'student.read_basic')"
            ),
            {"t": tid, "r": ids["role"]},
        )
    with (
        pytest.raises(IntegrityError, match="platform permissions cannot be granted"),
        tenant_session(tid) as s,
    ):
        s.execute(
            text(
                "INSERT INTO core.role_permissions (tenant_id, role_id, permission_key) "
                "VALUES (:t, :r, 'platform.tenants.read')"
            ),
            {"t": tid, "r": ids["role"]},
        )
    with (
        pytest.raises(IntegrityError, match="platform permissions cannot be granted"),
        tenant_session(tid) as s,
    ):
        s.execute(
            text(
                "UPDATE core.role_permissions SET permission_key = 'platform.tenants.read' "
                "WHERE role_id = :r"
            ),
            {"r": ids["role"]},
        )


def test_FR_IAM_011_platform_flag_must_match_key_prefix(admin_engine: Engine) -> None:
    with (
        pytest.raises(IntegrityError, match="permissions_platform_prefix"),
        admin_engine.begin() as c,
    ):
        c.execute(
            text(
                "INSERT INTO core.permissions (key, description, is_platform) "
                "VALUES ('platform.sneaky', 'x', false)"
            )
        )
    with (
        pytest.raises(IntegrityError, match="permissions_platform_prefix"),
        admin_engine.begin() as c,
    ):
        c.execute(
            text(
                "INSERT INTO core.permissions (key, description, is_platform) "
                "VALUES ('student.sneaky', 'x', true)"
            )
        )


# --- membership scopes -------------------------------------------------------------------


def _add_scope(
    tenant: uuid.UUID, membership: uuid.UUID, scope_type: str, ref: uuid.UUID | None
) -> None:
    with tenant_session(tenant) as s:
        s.execute(
            text(
                "INSERT INTO core.membership_scopes "
                "(id, tenant_id, membership_id, scope_type, scope_ref) "
                "VALUES (gen_random_uuid(), :t, :m, :st, :r)"
            ),
            {"t": tenant, "m": membership, "st": scope_type, "r": ref},
        )


def test_FR_IAM_012_scope_accepts_own_class_and_section(
    make_tenant: MakeTenant, make_member: MakeMember
) -> None:
    tid = make_tenant()
    ids = _seed_structure(tid)
    _, mid = make_member(tid)
    _add_scope(tid, mid, "school", None)
    _add_scope(tid, mid, "class", ids["class"])
    _add_scope(tid, mid, "section", ids["section"])
    with tenant_session(tid) as s:
        assert s.execute(text("SELECT count(*) FROM core.membership_scopes")).scalar() == 3


def test_FR_IAM_012_scope_rejects_section_of_another_tenant(
    make_tenant: MakeTenant, make_member: MakeMember
) -> None:
    a, b = make_tenant(), make_tenant()
    b_ids = _seed_structure(b)
    _seed_structure(a)
    _, mid = make_member(a)
    with pytest.raises(IntegrityError, match="not a section of this school"):
        _add_scope(a, mid, "section", b_ids["section"])
    with pytest.raises(IntegrityError, match="not a class of this school"):
        _add_scope(a, mid, "class", b_ids["class"])
    with pytest.raises(IntegrityError, match="not a section of this school"):
        _add_scope(a, mid, "section", uuid.uuid4())


def test_FR_IAM_012_scope_ref_must_match_type(
    make_tenant: MakeTenant, make_member: MakeMember
) -> None:
    tid = make_tenant()
    ids = _seed_structure(tid)
    _, mid = make_member(tid)
    with pytest.raises(IntegrityError, match="membership_scopes_ref_matches_type"):
        _add_scope(tid, mid, "school", ids["class"])
    with pytest.raises(IntegrityError, match="membership_scopes_ref_matches_type"):
        _add_scope(tid, mid, "section", None)
    # A class id is not a section id.
    with pytest.raises(IntegrityError, match="not a section of this school"):
        _add_scope(tid, mid, "section", ids["class"])


def test_FR_IAM_012_scoped_section_cannot_be_deleted(
    make_tenant: MakeTenant, make_member: MakeMember
) -> None:
    tid = make_tenant()
    ids = _seed_structure(tid)
    _, mid = make_member(tid)
    _add_scope(tid, mid, "section", ids["section"])
    with (
        pytest.raises(IntegrityError, match="still used as a membership scope"),
        tenant_session(tid) as s,
    ):
        s.execute(text("DELETE FROM core.sections WHERE id = :s"), {"s": ids["section"]})


def test_FR_IAM_012_duplicate_school_scope_rejected(
    make_tenant: MakeTenant, make_member: MakeMember
) -> None:
    tid = make_tenant()
    _, mid = make_member(tid)
    _add_scope(tid, mid, "school", None)
    with pytest.raises(IntegrityError, match="membership_scopes_unique_scope"):
        _add_scope(tid, mid, "school", None)


def test_FR_TEN_010_removing_class_teacher_membership_clears_section(
    make_tenant: MakeTenant, make_member: MakeMember
) -> None:
    tid = make_tenant()
    ids = _seed_structure(tid)
    _, mid = make_member(tid)
    with tenant_session(tid) as s:
        s.execute(
            text("UPDATE core.sections SET class_teacher_membership_id = :m WHERE id = :s"),
            {"m": mid, "s": ids["section"]},
        )
        s.execute(text("DELETE FROM core.memberships WHERE id = :m"), {"m": mid})
        row = s.execute(
            text("SELECT tenant_id, class_teacher_membership_id FROM core.sections WHERE id = :s"),
            {"s": ids["section"]},
        ).one()
    assert (row.tenant_id, row.class_teacher_membership_id) == (tid, None)
