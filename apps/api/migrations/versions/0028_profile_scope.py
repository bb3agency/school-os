"""Profiles shared across schools: ``core.user_membership_count(p_user)`` (ADR-0028; US-102,
FR-IAM-010, ADR-0013).

A person's profile (display name, email, language) lives in ``core.users`` and is shared by every
school the person belongs to. A school may edit it only when the person belongs to that school
alone (owner decision 2026-09-27); ``PATCH /users/{id}`` otherwise answers
``409 profile_shared``. Counting memberships across schools crosses tenants, so it needs an
allowlisted definer function. It returns ONLY an integer:

- the caller must have tenant context (``insufficient_privilege`` otherwise);
- ``p_user`` must have a membership in the current school, else NULL (no probing of people in
  other schools);
- the count is of schools with a membership of any status (a removed member still sees the
  profile in that school's history and staff list).

Owned by ``sos_definer`` (NOLOGIN, NOBYPASSRLS), ``search_path`` pinned, EXECUTE only for
``sos_app``; reads only ``core.memberships`` (``definer_access`` already exists, SELECT already
granted). Expand-only; downgrade drops the function.

Revision ID: 0028_profile_scope
Revises: 0027_identity_issuer
Create Date: 2026-09-27
"""

from __future__ import annotations

from alembic import op

revision = "0028_profile_scope"
down_revision = "0027_identity_issuer"
branch_labels = None
depends_on = None

SIGNATURE = "core.user_membership_count(uuid)"

FUNCTION_SQL = r"""
CREATE FUNCTION core.user_membership_count(p_user uuid) RETURNS integer
  LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE
  v_tenant uuid := core.current_tenant();
BEGIN
  IF v_tenant IS NULL THEN
    RAISE EXCEPTION 'tenant context is required' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF p_user IS NULL OR NOT EXISTS (
      SELECT 1 FROM core.memberships AS m
      WHERE m.tenant_id = v_tenant AND m.user_id = p_user) THEN
    RETURN NULL;
  END IF;
  RETURN (SELECT pg_catalog.count(*)::integer FROM core.memberships AS m
          WHERE m.user_id = p_user);
END
$$;
"""


def upgrade() -> None:
    op.execute("GRANT CREATE ON SCHEMA core TO sos_definer")
    op.execute("SET ROLE sos_definer")
    op.execute(FUNCTION_SQL)
    op.execute(f"REVOKE ALL ON FUNCTION {SIGNATURE} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {SIGNATURE} TO sos_app")
    op.execute("SET ROLE sos_owner")
    op.execute("REVOKE CREATE ON SCHEMA core FROM sos_definer")


def downgrade() -> None:
    op.execute(f"DROP FUNCTION IF EXISTS {SIGNATURE}")
