"""Composite tenant foreign keys (ADR-0013 §7, SEC-001, FR-TEN-001).

PostgreSQL FK checks bypass RLS. With a single-column FK a row in tenant A could reference a row
of tenant B whose UUID leaked. Every tenant->tenant FK therefore includes ``tenant_id``.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from app.core.db import tenant_session
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.db

TENANT_SCHEMAS = ["core", "sis", "kb", "audit", "ops"]


def test_ADR_0013_every_tenant_to_tenant_fk_is_composite(admin_engine: Engine) -> None:
    """Catalog check: an FK between two tables that both carry tenant_id must pair tenant_id."""
    with admin_engine.connect() as c:
        rows = c.execute(
            text(
                """
                SELECT con.conname, cn.nspname || '.' || cl.relname AS child,
                       pn.nspname || '.' || pl.relname AS parent,
                       ARRAY(SELECT a.attname::text FROM unnest(con.conkey) WITH ORDINALITY k(n, o)
                             JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.n
                             ORDER BY k.o) AS child_cols,
                       ARRAY(SELECT a.attname::text FROM unnest(con.confkey) WITH ORDINALITY k(n, o)
                             JOIN pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = k.n
                             ORDER BY k.o) AS parent_cols
                FROM pg_constraint con
                JOIN pg_class cl ON cl.oid = con.conrelid
                JOIN pg_namespace cn ON cn.oid = cl.relnamespace
                JOIN pg_class pl ON pl.oid = con.confrelid
                JOIN pg_namespace pn ON pn.oid = pl.relnamespace
                WHERE con.contype = 'f' AND cn.nspname = ANY(:schemas)
                  AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = con.conrelid
                              AND a.attname = 'tenant_id' AND NOT a.attisdropped)
                  AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = con.confrelid
                              AND a.attname = 'tenant_id' AND NOT a.attisdropped)
                """
            ),
            {"schemas": TENANT_SCHEMAS},
        ).all()
    assert rows, "expected tenant->tenant foreign keys in the catalog"
    bad = [
        f"{r.child}.{r.conname} -> {r.parent}"
        for r in rows
        if "tenant_id" not in r.child_cols
        or r.parent_cols[r.child_cols.index("tenant_id")] != "tenant_id"
    ]
    assert bad == []


def test_ADR_0013_core_tables_with_id_have_unique_tenant_id_id(admin_engine: Engine) -> None:
    with admin_engine.connect() as c:
        missing: list[str] = list(
            c.execute(
                text(
                    """
                    SELECT n.nspname || '.' || t.relname
                    FROM pg_class t JOIN pg_namespace n ON n.oid = t.relnamespace
                    WHERE n.nspname = 'core' AND t.relkind = 'r'
                      AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = t.oid
                                  AND a.attname = 'tenant_id' AND NOT a.attisdropped)
                      AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = t.oid
                                  AND a.attname = 'id' AND NOT a.attisdropped)
                      AND NOT EXISTS (
                        SELECT 1 FROM pg_constraint con
                        WHERE con.conrelid = t.oid AND con.contype IN ('u', 'p')
                          AND ARRAY(SELECT a.attname::text
                                    FROM unnest(con.conkey) WITH ORDINALITY k(n, o)
                                    JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.n
                                    ORDER BY k.o)
                              = ARRAY['tenant_id', 'id']::text[])
                    """
                )
            ).scalars()
        )
    assert missing == []


# --- concrete cross-tenant reference attempts as sos_app -----------------------------------


def _admin_exec(admin: Engine, sql: str, **params: Any) -> None:
    with admin.begin() as c:
        c.execute(text(sql), params)


@pytest.fixture
def two_schools(admin_engine: Engine, app_engine: Engine) -> dict[str, dict[str, uuid.UUID]]:
    """Two tenants, each with a user+membership, role, year, class and section."""
    out: dict[str, dict[str, uuid.UUID]] = {}
    for label in ("a", "b"):
        ids = {k: uuid.uuid4() for k in ("tenant", "user", "membership", "role", "year", "class")}
        ids["section"] = uuid.uuid4()
        _admin_exec(
            admin_engine,
            "INSERT INTO core.tenants (id, code, name, status) VALUES (:t, :c, 'S', 'active')",
            t=ids["tenant"],
            c=f"t-{uuid.uuid4().hex[:12]}",
        )
        _admin_exec(
            admin_engine,
            "INSERT INTO core.users (id, idp_subject, display_name) VALUES (:u, :s, 'U')",
            u=ids["user"],
            s=f"sub-{uuid.uuid4().hex}",
        )
        _admin_exec(
            admin_engine,
            "INSERT INTO core.memberships (id, tenant_id, user_id, status) "
            "VALUES (:m, :t, :u, 'active')",
            m=ids["membership"],
            t=ids["tenant"],
            u=ids["user"],
        )
        _admin_exec(
            admin_engine,
            "INSERT INTO core.roles (id, tenant_id, key, name_en, name_te) "
            "VALUES (:r, :t, 'teacher', 'Teacher', 'ఉపాధ్యాయుడు')",
            r=ids["role"],
            t=ids["tenant"],
        )
        _admin_exec(
            admin_engine,
            "INSERT INTO core.academic_years (id, tenant_id, label, starts_on, ends_on) "
            "VALUES (:y, :t, '2026-27', '2026-06-01', '2027-04-30')",
            y=ids["year"],
            t=ids["tenant"],
        )
        _admin_exec(
            admin_engine,
            "INSERT INTO core.classes (id, tenant_id, code, display_en, display_te, sort_order) "
            "VALUES (:c, :t, 'IX', 'Class IX', '9వ తరగతి', 12)",
            c=ids["class"],
            t=ids["tenant"],
        )
        _admin_exec(
            admin_engine,
            "INSERT INTO core.sections (id, tenant_id, class_id, academic_year_id, name) "
            "VALUES (:s, :t, :c, :y, 'A')",
            s=ids["section"],
            t=ids["tenant"],
            c=ids["class"],
            y=ids["year"],
        )
        out[label] = ids
    return out


CROSS_TENANT_INSERTS = {
    "membership_roles.role": (
        "INSERT INTO core.membership_roles (tenant_id, membership_id, role_id) "
        "VALUES (:ta, :a_membership, :b_role)",
        "membership_roles_role_fk",
    ),
    "membership_roles.membership": (
        "INSERT INTO core.membership_roles (tenant_id, membership_id, role_id) "
        "VALUES (:ta, :b_membership, :a_role)",
        "membership_roles_membership_fk",
    ),
    "role_permissions.role": (
        "INSERT INTO core.role_permissions (tenant_id, role_id, permission_key) "
        "VALUES (:ta, :b_role, 'student.read_basic')",
        "role_permissions_role_fk",
    ),
    "membership_scopes.membership": (
        "INSERT INTO core.membership_scopes (id, tenant_id, membership_id, scope_type) "
        "VALUES (gen_random_uuid(), :ta, :b_membership, 'school')",
        "membership_scopes_membership_fk",
    ),
    "sections.class": (
        "INSERT INTO core.sections (id, tenant_id, class_id, academic_year_id, name) "
        "VALUES (gen_random_uuid(), :ta, :b_class, :a_year, 'Z')",
        "sections_class_fk",
    ),
    "sections.academic_year": (
        "INSERT INTO core.sections (id, tenant_id, class_id, academic_year_id, name) "
        "VALUES (gen_random_uuid(), :ta, :a_class, :b_year, 'Z')",
        "sections_academic_year_fk",
    ),
    "sections.class_teacher": (
        "UPDATE core.sections SET class_teacher_membership_id = :b_membership "
        "WHERE id = :a_section",
        "sections_class_teacher_fk",
    ),
}


@pytest.mark.parametrize("case", sorted(CROSS_TENANT_INSERTS))
def test_ADR_0013_cannot_reference_other_tenants_row_by_known_id(
    case: str, two_schools: dict[str, dict[str, uuid.UUID]], admin_engine: Engine
) -> None:
    a, b = two_schools["a"], two_schools["b"]
    _admin_exec(
        admin_engine,
        "INSERT INTO core.permissions (key, description) VALUES ('student.read_basic', 'Read') "
        "ON CONFLICT DO NOTHING",
    )
    sql, constraint = CROSS_TENANT_INSERTS[case]
    params = {"ta": a["tenant"]}
    params.update({f"a_{k}": v for k, v in a.items()})
    params.update({f"b_{k}": v for k, v in b.items()})
    with pytest.raises(IntegrityError, match=constraint), tenant_session(a["tenant"]) as s:
        s.execute(text(sql), params)


def test_ADR_0013_same_tenant_references_are_accepted(
    two_schools: dict[str, dict[str, uuid.UUID]],
) -> None:
    a = two_schools["a"]
    with tenant_session(a["tenant"]) as s:
        s.execute(
            text(
                "INSERT INTO core.membership_roles (tenant_id, membership_id, role_id) "
                "VALUES (:t, :m, :r)"
            ),
            {"t": a["tenant"], "m": a["membership"], "r": a["role"]},
        )
        s.execute(
            text("UPDATE core.sections SET class_teacher_membership_id = :m WHERE id = :s"),
            {"m": a["membership"], "s": a["section"]},
        )
        assert s.execute(text("SELECT count(*) FROM core.membership_roles")).scalar_one() == 1
