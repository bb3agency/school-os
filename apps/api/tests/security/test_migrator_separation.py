"""Migrator role separation (SEC-002, docs/07 §7).

The runtime roles (``sos_app``, ``sos_platform``, ``sos_readonly``) never run DDL: they cannot
create objects in any schema, own no relation and own no schema. Schema changes go only through
``sos_migrator`` (``make migrate``). The superuser engine is used for catalog reads only.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import ProgrammingError

pytestmark = pytest.mark.db

RUNTIME_ROLES = ("sos_app", "sos_platform", "sos_readonly")


def _app_schemas(admin_engine: Engine) -> list[str]:
    with admin_engine.connect() as conn:
        return list(
            conn.execute(
                text(
                    "SELECT nspname FROM pg_namespace "
                    "WHERE nspname NOT LIKE 'pg\\_%' AND nspname <> 'information_schema' "
                    "ORDER BY 1"
                )
            ).scalars()
        )


def test_SEC_002_runtime_roles_cannot_create_objects_in_any_schema(admin_engine: Engine) -> None:
    schemas = _app_schemas(admin_engine)
    assert "core" in schemas
    problems: list[str] = []
    with admin_engine.connect() as conn:
        for role in RUNTIME_ROLES:
            for schema in schemas:
                can_create: bool = conn.execute(
                    text("SELECT has_schema_privilege(:r, :s, 'CREATE')"),
                    {"r": role, "s": schema},
                ).scalar_one()
                if can_create:
                    problems.append(f"{role} has CREATE on schema {schema}")
            can_create_db: bool = conn.execute(
                text("SELECT has_database_privilege(:r, current_database(), 'CREATE')"),
                {"r": role},
            ).scalar_one()
            if can_create_db:
                problems.append(f"{role} has CREATE on the database")
    assert problems == []


def test_SEC_002_runtime_roles_own_no_relation_or_schema(admin_engine: Engine) -> None:
    with admin_engine.connect() as conn:
        owned_relations = conn.execute(
            text(
                "SELECT r.rolname, n.nspname || '.' || c.relname "
                "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "JOIN pg_roles r ON r.oid = c.relowner WHERE r.rolname = ANY(:roles)"
            ),
            {"roles": list(RUNTIME_ROLES)},
        ).all()
        owned_schemas = conn.execute(
            text(
                "SELECT r.rolname, n.nspname FROM pg_namespace n "
                "JOIN pg_roles r ON r.oid = n.nspowner WHERE r.rolname = ANY(:roles)"
            ),
            {"roles": list(RUNTIME_ROLES)},
        ).all()
    assert [tuple(r) for r in owned_relations] == []
    assert [tuple(r) for r in owned_schemas] == []


def test_SEC_002_app_role_ddl_is_refused(app_engine: Engine) -> None:
    with app_engine.connect() as conn:
        with pytest.raises(ProgrammingError, match="permission denied"):
            conn.execute(text("CREATE TABLE core.sec002_probe (id int)"))
        conn.rollback()
