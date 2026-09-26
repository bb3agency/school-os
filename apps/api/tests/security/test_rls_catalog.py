"""RLS catalog test (SEC-001, SEC-002, FR-TEN-002, ADR-0013, docs/12 §4.5).

Fails when any table in a tenant schema lacks ENABLE + FORCE RLS and its isolation policy,
when the definer escape hatch appears on an unlisted table, when a SECURITY DEFINER function
is not allowlisted, or when the platform/app privilege separation is broken.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy import Connection, Engine, text

ALLOWLIST: dict[str, Any] = yaml.safe_load(
    (Path(__file__).parent / "rls_allowlist.yaml").read_text(encoding="utf-8")
)

pytestmark = pytest.mark.db


def catalog_violations(conn: Connection, schemas: list[str]) -> list[str]:
    """Return human-readable violations for tables in ``schemas`` (pure catalog query)."""
    rows = conn.execute(
        text(
            """
            SELECT n.nspname AS schema, c.relname AS name, c.relkind,
                   c.relrowsecurity AS rls, c.relforcerowsecurity AS force,
                   EXISTS (SELECT 1 FROM pg_attribute a
                           WHERE a.attrelid = c.oid AND a.attname = 'tenant_id'
                             AND NOT a.attisdropped) AS has_tenant,
                   ARRAY(SELECT p.polname::text FROM pg_policy p
                         WHERE p.polrelid = c.oid) AS policies
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = ANY(:schemas) AND c.relkind IN ('r', 'p')
            ORDER BY 1, 2
            """
        ),
        {"schemas": schemas},
    ).mappings()
    variants: dict[str, str] = ALLOWLIST["policy_variants"]
    globals_: dict[str, str] = ALLOWLIST["global_tables"]
    definer_ok = set(ALLOWLIST["definer_access_tables"])
    problems: list[str] = []
    for r in rows:
        fq = f"{r['schema']}.{r['name']}"
        policies = set(r["policies"])
        if "definer_access" in policies and fq not in definer_ok:
            problems.append(f"{fq}: definer_access policy not allowlisted")
        if not r["has_tenant"] and fq not in variants:
            if fq not in globals_:
                problems.append(f"{fq}: no tenant_id column and not listed in global_tables")
            continue
        if not (r["rls"] and r["force"]):
            problems.append(f"{fq}: RLS must be ENABLED and FORCED")
        expected = variants.get(fq, "tenant_isolation")
        if expected not in policies:
            problems.append(f"{fq}: missing policy {expected}")
    return problems


def test_SEC_001_every_tenant_table_has_forced_rls_and_policy(
    app_engine: Engine, admin_engine: Engine
) -> None:
    with admin_engine.connect() as conn:
        problems = catalog_violations(conn, ALLOWLIST["tenant_schemas"])
    assert problems == []


def test_SEC_001_catalog_check_detects_a_bad_table(admin_engine: Engine) -> None:
    """Self-test: the check must flag a tenant table without RLS (guards against a vacuous pass)."""
    schema = f"rls_selftest_{uuid.uuid4().hex[:8]}"
    with admin_engine.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA {schema}"))
        conn.execute(text(f"CREATE TABLE {schema}.leaky (id uuid, tenant_id uuid NOT NULL)"))
        conn.execute(text(f"CREATE TABLE {schema}.no_force (id uuid, tenant_id uuid NOT NULL)"))
        conn.execute(text(f"ALTER TABLE {schema}.no_force ENABLE ROW LEVEL SECURITY"))
        conn.execute(text(f"CREATE TABLE {schema}.orphan_global (id uuid)"))
        try:
            problems = catalog_violations(conn, [schema])
        finally:
            conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
    joined = "\n".join(problems)
    assert f"{schema}.leaky: RLS must be ENABLED and FORCED" in joined
    assert f"{schema}.no_force: RLS must be ENABLED and FORCED" in joined
    assert f"{schema}.leaky: missing policy tenant_isolation" in joined
    assert f"{schema}.orphan_global: no tenant_id column" in joined


def test_SEC_002_no_application_role_is_superuser_or_bypasses_rls(admin_engine: Engine) -> None:
    with admin_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT rolname, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb "
                "FROM pg_roles WHERE rolname LIKE 'sos\\_%'"
            )
        ).all()
    names = {r.rolname for r in rows}
    assert {
        "sos_owner",
        "sos_migrator",
        "sos_app",
        "sos_platform",
        "sos_readonly",
        "sos_definer",
    } <= names
    for r in rows:
        assert not r.rolsuper, r.rolname
        assert not r.rolbypassrls, r.rolname
        assert not r.rolcreaterole, r.rolname
        assert not r.rolcreatedb, r.rolname


def test_SEC_002_app_and_platform_roles_cannot_become_owner_or_definer(
    admin_engine: Engine,
) -> None:
    with admin_engine.connect() as conn:
        for member in ("sos_app", "sos_platform", "sos_readonly"):
            for target in ("sos_owner", "sos_definer", "sos_migrator"):
                can: bool = conn.execute(
                    text("SELECT pg_has_role(:m, :t, 'MEMBER')"), {"m": member, "t": target}
                ).scalar_one()
                assert can is False, f"{member} must not be a member of {target}"


def test_ADR_0013_security_definer_functions_are_allowlisted(admin_engine: Engine) -> None:
    with admin_engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT n.nspname || '.' || p.proname AS fq, r.rolname AS owner,
                       coalesce(array_to_string(p.proconfig, ','), '') AS config
                FROM pg_proc p
                JOIN pg_namespace n ON n.oid = p.pronamespace
                JOIN pg_roles r ON r.oid = p.proowner
                WHERE p.prosecdef AND n.nspname = ANY(:schemas)
                """
            ),
            {"schemas": [*ALLOWLIST["tenant_schemas"], "platform"]},
        ).all()
    allowed = set(ALLOWLIST["definer_functions"])
    for r in rows:
        assert r.fq in allowed, f"SECURITY DEFINER function {r.fq} is not allowlisted"
        assert r.owner == "sos_definer", f"{r.fq} must be owned by sos_definer"
        assert "search_path=" in r.config, f"{r.fq} must pin search_path"


def test_ADR_0013_platform_role_has_no_access_to_tenant_tables(admin_engine: Engine) -> None:
    with admin_engine.connect() as conn:
        leaked: list[str] = list(
            conn.execute(
                text(
                    """
                SELECT n.nspname || '.' || c.relname
                FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = ANY(:schemas) AND c.relkind IN ('r','p','v','m')
                  AND (has_table_privilege('sos_platform', c.oid, 'SELECT')
                    OR has_table_privilege('sos_platform', c.oid, 'INSERT')
                    OR has_table_privilege('sos_platform', c.oid, 'UPDATE')
                    OR has_table_privilege('sos_platform', c.oid, 'DELETE'))
                """
                ),
                {"schemas": ALLOWLIST["tenant_schemas"]},
            )
            .scalars()
            .all()
        )
    assert leaked == []


def test_ADR_0013_app_role_cannot_touch_platform_tables(admin_engine: Engine) -> None:
    readable = set(ALLOWLIST["platform_tables_readable_by_app"])
    with admin_engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT n.nspname || '.' || c.relname AS fq, r.role,
                       has_table_privilege(r.role, c.oid, 'SELECT') AS sel,
                       has_table_privilege(r.role, c.oid, 'INSERT')
                       OR has_table_privilege(r.role, c.oid, 'UPDATE')
                       OR has_table_privilege(r.role, c.oid, 'DELETE') AS write
                FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                CROSS JOIN (VALUES ('sos_app'), ('sos_readonly')) AS r(role)
                WHERE n.nspname = 'platform' AND c.relkind IN ('r','p','v','m')
                """
            )
        ).all()
    for r in rows:
        assert not r.write, f"{r.role} must not write {r.fq}"
        if r.sel:
            assert r.role == "sos_app", f"{r.role} must not read {r.fq}"
            assert r.fq in readable, f"sos_app must not read {r.fq}"
