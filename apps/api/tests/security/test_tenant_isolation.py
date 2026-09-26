"""Generic cross-tenant checks over EVERY tenant table in the catalog (docs/12 §4.4).

New tables are covered automatically as migrations add them.
"""

from __future__ import annotations

import pytest
from app.core.db import context_free_session
from sqlalchemy import Engine, text

pytestmark = pytest.mark.db


def _tenant_tables(admin_engine: Engine) -> list[str]:
    with admin_engine.connect() as conn:
        return list(
            conn.execute(
                text(
                    """
                    SELECT n.nspname || '.' || c.relname
                    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname IN ('core','sis','kb','audit','ops')
                      AND c.relkind IN ('r','p') AND NOT c.relispartition
                      AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid
                                  AND a.attname IN ('tenant_id') AND NOT a.attisdropped)
                      AND has_table_privilege('sos_app', c.oid, 'SELECT')
                    ORDER BY 1
                    """
                )
            ).scalars()
        )


def test_FR_TEN_002_every_tenant_table_is_empty_without_context(
    admin_engine: Engine, app_engine: Engine
) -> None:
    tables = _tenant_tables(admin_engine)
    visible: dict[str, int] = {}
    with context_free_session() as s:
        for table in tables:
            n = int(s.execute(text(f"SELECT count(*) FROM {table}")).scalar_one())
            if n:
                visible[table] = n
    assert visible == {}
