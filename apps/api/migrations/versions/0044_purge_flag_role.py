"""The offboarding purge flags count only for the purge role (ADR-0029; audit 2026-10-04 DL-05).

``core.tenant_purge_allowed()`` and ``core.tenant_audit_purge_allowed()`` (0032) checked the
tenant context, the school's status and a transaction-local setting (``app.purge_tenant`` /
``app.purge_audit``). A setting is not a privilege: any role may set it. The row guards that
ask these functions (``sis.tg_students_delete_only_by_import_revert`` and
``sis.tg_certificates_append_only``) therefore let ``sos_app`` itself, which holds DELETE on
``sis.students`` for import reverts, remove students of an ``offboarding`` school outside an
import revert. The RLS policies ``offboarding_purge`` already apply only to ``sos_purger``.

Both functions now also require ``current_user = 'sos_purger'`` (the role ``core.purge``
switches to with ``SET LOCAL ROLE``; the functions are SECURITY INVOKER, so ``current_user``
is the caller). Nothing else changes: same signatures, grants, comments and callers.

Downgrade restores the 0032 bodies.

Revision ID: 0044_purge_flag_role
Revises: 0043_plan_row_version
Create Date: 2026-10-04
"""

from __future__ import annotations

from alembic import op

revision = "0044_purge_flag_role"
down_revision = "0043_plan_row_version"
branch_labels = None
depends_on = None

FUNCTIONS_SQL = """
CREATE OR REPLACE FUNCTION core.tenant_purge_allowed() RETURNS boolean
  LANGUAGE sql STABLE SECURITY INVOKER SET search_path = pg_catalog, pg_temp AS $$
  SELECT coalesce(
    /*ROLE*/core.current_tenant() IS NOT NULL
    AND current_setting('app.purge_tenant', true) = core.current_tenant()::text
    AND EXISTS (SELECT 1 FROM core.tenants AS t
                WHERE t.id = core.current_tenant() AND t.status = 'offboarding'),
    false)
$$;

CREATE OR REPLACE FUNCTION core.tenant_audit_purge_allowed() RETURNS boolean
  LANGUAGE sql STABLE SECURITY INVOKER SET search_path = pg_catalog, pg_temp AS $$
  SELECT coalesce(
    /*ROLE*/core.current_tenant() IS NOT NULL
    AND current_setting('app.purge_audit', true) = core.current_tenant()::text
    AND EXISTS (SELECT 1 FROM core.tenants AS t
                WHERE t.id = core.current_tenant() AND t.status = 'deleted'),
    false)
$$;
"""
ROLE_CHECK = "current_user = 'sos_purger'\n    AND "


def upgrade() -> None:
    op.execute(FUNCTIONS_SQL.replace("/*ROLE*/", ROLE_CHECK))


def downgrade() -> None:
    op.execute(FUNCTIONS_SQL.replace("/*ROLE*/", ""))
